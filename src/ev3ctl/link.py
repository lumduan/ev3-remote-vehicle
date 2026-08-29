"""HOST CODE. The SSH transport, and the only place that knows the wire.

One SSH process runs `python3 -u /tmp/ev3_agent.py` on the brick with
pipes on its stdin, stdout and stderr. Requests and responses are
newline-delimited JSON, one object per line, strictly one response per
request, never pipelined. The agent's stderr is a separate pipe so that
a human-readable warning from the brick can never be mistaken for a
protocol frame.

The API comes in two halves on purpose. `request()` blocks and is what
one-shot commands and teardown use. `send()` plus `pump()` do not block
at all, and are what the live loop uses so that a slow round trip on a
300 MHz brick never freezes key handling. Both share one reader, so a
response is a response no matter which half is waiting for it.
"""

import errno
import json
import os
import select
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .errors import AgentError, Diagnosis, LinkError, quote_command

DEFAULT_HOST = "robot@ev3dev.local"
REMOTE_AGENT_PATH = "/tmp/ev3_agent.py"
AGENT_FILENAME = "ev3_agent.py"

DEFAULT_TIMEOUT_S = 8.0
CONNECT_TIMEOUT_S = 10
READ_CHUNK = 65536

# sockaddr_un.sun_path is 104 bytes on macOS. Leave a little room.
CONTROL_PATH_LIMIT = 100


def find_agent_source(override=None):
    """Locate agent/ev3_agent.py.

    agent/ is deliberately not packaged - it is source for a different
    interpreter on a different machine - so it is found on disk rather
    than through importlib. It is never imported.
    """
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise LinkError(Diagnosis(
                summary="No agent source at {0}".format(path),
                cause="--agent was given a path that is not a file.",
                checklist=("check the path passed to --agent",),
            ))
        return path

    candidates = [
        Path(__file__).resolve().parents[2] / "agent" / AGENT_FILENAME,
        Path.cwd() / "agent" / AGENT_FILENAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise LinkError(Diagnosis(
        summary="Could not find agent/{0}".format(AGENT_FILENAME),
        cause=(
            "This file runs on the brick and is copied there at startup. "
            "It is not part of the installed package, so it is looked up "
            "on disk relative to the repository."
        ),
        detail="\n".join("tried: " + str(c) for c in candidates),
        checklist=(
            "run ev3ctl from inside the repository, or",
            "pass --agent /path/to/agent/ev3_agent.py",
        ),
    ))


class Link(object):
    """One SSH session to one brick."""

    def __init__(self, host=DEFAULT_HOST, agent_source=None,
                 timeout=DEFAULT_TIMEOUT_S, multiplex=True):
        self.host = host
        self.timeout = timeout
        self._agent_source = find_agent_source(agent_source)
        self._multiplex = multiplex
        self._control_dir = None
        self._control_path = None
        self._process = None
        self._argv = []
        self._buffer = b""
        self._stderr_buffer = b""
        self._pending = {}
        self._next_id = 1
        self._closed = False
        self.hello = {}

    # -- ssh plumbing -------------------------------------------------

    def _ssh_options(self):
        options = ["-o", "ConnectTimeout={0}".format(CONNECT_TIMEOUT_S)]
        if self._control_path:
            options += [
                "-o", "ControlMaster=auto",
                "-o", "ControlPath=" + self._control_path,
                "-o", "ControlPersist=30",
            ]
        return options

    def _start_multiplexing(self):
        """Set up a shared SSH connection, or quietly do without one.

        A control socket is a unix domain socket, so the whole path must
        fit in sockaddr_un.sun_path - 104 bytes on macOS. Two things
        conspire against that: the default temp directory here is
        /var/folders/<two long random components>/T/, and ssh's %C token
        expands to a 40-character hash. Together they overflow, and ssh
        fails the connection outright rather than falling back.

        So the socket goes in /tmp under a short fixed name, and if even
        that would not fit, multiplexing is dropped. It is a latency
        optimisation; it is not worth failing to reach the brick for.
        """
        if not self._multiplex:
            return
        try:
            control_dir = tempfile.mkdtemp(prefix="ev3ctl-", dir="/tmp")
        except OSError:
            return
        control_path = os.path.join(control_dir, "s")
        if len(control_path) >= CONTROL_PATH_LIMIT:
            shutil.rmtree(control_dir, ignore_errors=True)
            return
        self._control_dir = control_dir
        self._control_path = control_path

    def _copy_agent(self):
        """Send the agent to the brick over the SSH channel itself.

        `cat >` rather than scp on purpose. Modern OpenSSH runs scp over
        SFTP, which needs sftp-server present on the far end; `cat` needs
        only a shell, and the brick's image has not been checked for
        either. Fewer assumptions, and it reuses the control connection.
        """
        argv = (["ssh"] + self._ssh_options() + [self.host,
                "cat > " + REMOTE_AGENT_PATH])
        source = self._agent_source.read_bytes()
        try:
            proc = subprocess.run(
                argv, input=source, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=CONNECT_TIMEOUT_S + 15,
            )
        except subprocess.TimeoutExpired:
            raise LinkError(Diagnosis(
                summary="Timed out copying the agent to {0}".format(
                    self.host),
                cause="ssh did not finish within the connect timeout.",
                command=quote_command(argv),
            ))
        except OSError as exc:
            raise LinkError(Diagnosis(
                summary="Could not run ssh",
                cause=str(exc),
                command=quote_command(argv),
                checklist=("ssh is installed and on PATH",),
            ))
        if proc.returncode != 0:
            raise LinkError(Diagnosis(
                summary="Could not reach {0}".format(self.host),
                cause=(
                    "ssh exited with status {0} while copying the agent "
                    "to {1}.".format(proc.returncode, REMOTE_AGENT_PATH)
                ),
                command=quote_command(argv),
                detail=proc.stderr.decode("utf-8", "replace"),
            ))

    def _spawn_agent(self):
        argv = (["ssh"] + self._ssh_options() + [self.host,
                "python3 -u " + REMOTE_AGENT_PATH])
        try:
            self._process = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, bufsize=0,
            )
        except OSError as exc:
            raise LinkError(Diagnosis(
                summary="Could not start the agent on {0}".format(self.host),
                cause=str(exc),
                command=quote_command(argv),
            ))
        self._argv = argv

    def open(self):
        """Copy the agent, start it, and complete the handshake.

        Everything that can prompt for a password happens here, before
        the caller puts the terminal into cbreak mode or starts a Live
        display. A password prompt arriving underneath a full-screen
        render is a bad way to find out that ssh-copy-id was never run.
        """
        self._start_multiplexing()
        self._copy_agent()
        self._spawn_agent()
        try:
            self.hello = self.request("hello", timeout=self.timeout)
        except LinkError:
            self.close()
            raise
        return self.hello

    # -- reading ------------------------------------------------------

    @property
    def stdout_fd(self):
        return self._process.stdout.fileno()

    @property
    def stderr_fd(self):
        return self._process.stderr.fileno()

    def _read_fd(self, fd):
        try:
            return os.read(fd, READ_CHUNK)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return b""
            raise

    def _link_died(self):
        stderr = self.drain_stderr()
        returncode = self._process.poll()
        return LinkError(Diagnosis(
            summary="Lost the link to {0}".format(self.host),
            cause=(
                "The agent's stdout closed. ssh exited with status "
                "{0}.".format("still running" if returncode is None
                              else returncode)
            ),
            command=quote_command(getattr(self, "_argv", [])),
            detail=stderr or None,
        ))

    def pump(self):
        """Read whatever is available. Never blocks. Returns responses.

        Called from the live loop after select says the pipe is ready,
        and from request() while it waits. Both go through here so that
        a response arriving early is never dropped.
        """
        if self._closed or self._process is None:
            return []
        chunk = self._read_fd(self.stdout_fd)
        if chunk == b"":
            if self._process.poll() is not None:
                raise self._link_died()
            return []
        self._buffer += chunk
        responses = []
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                responses.append(json.loads(line.decode("utf-8", "replace")))
            except ValueError:
                # Not a protocol frame. The agent promises stdout is
                # JSON only, so this is worth surfacing rather than
                # dropping: it usually means a shell profile on the
                # brick printed a banner into our pipe.
                responses.append({
                    "id": None, "ok": False, "kind": "bad_frame",
                    "error": "unparseable line from agent: {0!r}".format(
                        line[:200]),
                })
        for response in responses:
            request_id = response.get("id")
            if request_id is not None:
                self._pending.pop(request_id, None)
        return responses

    def drain_stderr(self):
        """Everything the agent has written to stderr since last asked."""
        if self._process is None:
            return ""
        text = ""
        while True:
            ready, _, _ = select.select([self.stderr_fd], [], [], 0)
            if not ready:
                break
            chunk = self._read_fd(self.stderr_fd)
            if not chunk:
                break
            self._stderr_buffer += chunk
        if self._stderr_buffer:
            text = self._stderr_buffer.decode("utf-8", "replace")
            self._stderr_buffer = b""
        return text

    # -- writing ------------------------------------------------------

    def send(self, cmd, **fields):
        """Queue one command. Returns its id. Does not wait."""
        if self._closed or self._process is None:
            raise LinkError(Diagnosis(
                summary="The link is closed",
                cause="A command was sent after the session ended.",
            ))
        request_id = self._next_id
        self._next_id += 1
        payload = {"id": request_id, "cmd": cmd}
        payload.update(fields)
        line = (json.dumps(payload) + "\n").encode("utf-8")
        try:
            self._process.stdin.write(line)
            self._process.stdin.flush()
        except (BrokenPipeError, OSError):
            raise self._link_died()
        self._pending[request_id] = cmd
        return request_id

    def request(self, cmd, timeout=None, **fields):
        """Send one command and wait for its response.

        Raises AgentError when the agent refuses the command and
        LinkError when the link itself is the problem. The distinction
        matters: one of them means check a cable.
        """
        deadline_s = self.timeout if timeout is None else timeout
        request_id = self.send(cmd, **fields)
        return self.wait_for(request_id, deadline_s, cmd)

    def wait_for(self, request_id, timeout, cmd="?"):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LinkError(Diagnosis(
                    summary="The brick did not answer in {0:.0f}s".format(
                        timeout),
                    cause=(
                        "Sent {0!r} and no response with that id came "
                        "back.".format(cmd)
                    ),
                    detail=self.drain_stderr() or None,
                ))
            ready, _, _ = select.select(
                [self.stdout_fd], [], [], min(remaining, 0.2))
            if not ready:
                continue
            for response in self.pump():
                if response.get("id") == request_id:
                    return unwrap(response)
                # A response for some other id, which only happens when
                # the live loop's poll and a keypress command overlap.
                # Dropping it is correct: the live loop treats a poll it
                # never sees as one skipped frame.

    # -- teardown -----------------------------------------------------

    def close(self):
        """Shut the link down. Never raises.

        Called from finally blocks, where the link may already be dead
        and where raising would mask the exception that got us here.
        """
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except Exception:
                    pass
            try:
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        if self._control_path:
            try:
                subprocess.run(
                    ["ssh", "-o", "ControlPath=" + self._control_path,
                     "-O", "exit", self.host],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            except Exception:
                pass
        if self._control_dir:
            shutil.rmtree(self._control_dir, ignore_errors=True)


def unwrap(response):
    """Turn one response object into a result, or raise."""
    if response.get("ok"):
        return response.get("result") or {}
    raise AgentError(
        response.get("error") or "the agent refused the command",
        kind=response.get("kind") or "error",
    )
