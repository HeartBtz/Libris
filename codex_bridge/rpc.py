"""Private stdio protocol driver for pinned Codex; never exposes arbitrary RPC to HTTP callers."""

import asyncio
import contextlib
import json
import os
from pathlib import Path


class CodexError(Exception):
    pass


class CodexAuthRequired(CodexError):
    pass


class CodexUnavailable(CodexError):
    pass


class CodexQuotaExceeded(CodexUnavailable):
    pass


class CodexContentRefused(CodexError):
    pass


def turn_error(error: dict) -> CodexError:
    info = error.get("codexErrorInfo", "other")
    if info in ("cyberPolicy", "misalignmentPolicyViolation"):
        return CodexContentRefused("Codex a refusé la demande pour une restriction de contenu.")
    if info == "unauthorized":
        return CodexAuthRequired("Session Codex expirée ou non autorisée. Reconnectez le compte.")
    if info in ("usageLimitExceeded", "rateLimitExceeded"):
        return CodexQuotaExceeded("Quota Codex atteint ; une reprise différée est nécessaire.")
    if isinstance(info, dict):
        return CodexUnavailable("Flux Codex interrompu ou connexion au modèle indisponible.")
    if info in ("serverOverloaded", "internalServerError"):
        return CodexUnavailable("Service Codex temporairement indisponible.")
    return CodexError(f"Codex : {info}. Vérifiez modèle, contexte et paramètres du provider.")


class CodexSession:
    def __init__(self, home: Path):
        self.home = home
        self.process = None
        self.reader = None
        self.pending: dict[int, asyncio.Future] = {}
        self.events: dict[str, asyncio.Queue] = {}
        self.next_id = 0
        self.ready = False
        self.start_lock = asyncio.Lock()
        self.generation_lock = asyncio.Lock()
        self.generation_slots = asyncio.Semaphore(16)
        self.active_generations = 0
        self.turn_ids: dict[str, str] = {}

    async def start(self):
        async with self.start_lock:
            if (
                self.ready
                and self.process
                and self.process.returncode is None
                and self.reader
                and not self.reader.done()
            ):
                return
            if self.process:
                await self.close()
            self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = {
                "cli_auth_credentials_store": "file",
                "forced_login_method": "chatgpt",
                "web_search": "disabled",
                "mcp_servers": {},
                "update_plan_enabled": False,
                "experimental_request_user_input_enabled": False,
                "features.shell_tool": False,
                "features.view_image": False,
                "features.multi_agent": False,
                "features.apps": False,
                "features.plugins": False,
                "features.js_repl": False,
                "features.image_generation": False,
            }
            args = ["codex", "app-server"]
            for key, value in flags.items():
                # No caller input becomes a command-line option.
                args += ["-c", f"{key}={json.dumps(value, separators=(',', ':'))}"]
            env = {k: v for k, v in os.environ.items() if k in {"PATH", "LANG", "SSL_CERT_FILE"}}
            env.update(CODEX_HOME=str(self.home), HOME=str(self.home), RUST_LOG="off")
            self.process = await asyncio.create_subprocess_exec(
                *args,
                cwd="/tmp",
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                limit=4 * 1024**2,
            )
            self.reader = asyncio.create_task(self.read_loop())
            await self.call(
                "initialize",
                {
                    "clientInfo": {"name": "libris", "version": "0.5.0"},
                    "capabilities": {"experimentalApi": True},
                },
                timeout=30,
            )
            await self.send({"method": "initialized", "params": {}})
            self.ready = True

    async def send(self, message: dict):
        if not self.process or self.process.returncode is not None:
            raise CodexError("Processus Codex indisponible.")
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        await self.process.stdin.drain()

    async def call(self, method: str, params: dict, timeout: int = 30) -> dict:
        self.next_id += 1
        ident = self.next_id
        future = asyncio.get_running_loop().create_future()
        self.pending[ident] = future
        try:
            await self.send({"id": ident, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self.pending.pop(ident, None)

    async def read_loop(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message and "id" in message:
                    # No tool approval, command, patch, dynamic tool or credential request is authorized.
                    await self.send(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "This text translation client does not authorize tools.",
                            },
                        }
                    )
                elif "id" in message:
                    future = self.pending.get(message["id"])
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(
                                CodexError(
                                    f"Codex RPC refusé ({message['error'].get('code')}). Vérifiez connexion et modèle."
                                )
                            )
                        else:
                            future.set_result(message.get("result", {}))
                else:
                    params = message.get("params", {})
                    queue = self.events.get(params.get("threadId"))
                    if queue and message.get("method") == "turn/started":
                        self.turn_ids[params["threadId"]] = params.get("turn", {}).get("id", "")
                    # Deltas are not needed: completed items are authoritative and bound memory use.
                    if queue and message.get("method") in {
                        "item/completed",
                        "turn/completed",
                        "thread/tokenUsage/updated",
                    }:
                        queue.put_nowait(message)
        except (ValueError, OSError, asyncio.QueueFull):
            pass
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(CodexUnavailable("Connexion au processus Codex interrompue."))
            for queue in self.events.values():
                with contextlib.suppress(asyncio.QueueFull):
                    queue.put_nowait({"method": "disconnected", "params": {}})

    async def close(self):
        self.ready = False
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        if self.reader:
            self.reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.reader

    @contextlib.asynccontextmanager
    async def generation(self):
        async with self.generation_slots:
            async with self.generation_lock:
                self.active_generations += 1
            try:
                yield
            finally:
                self.active_generations -= 1

    async def complete(self, body: dict) -> dict:
        async with self.generation():
            await self.start()
            account = await self.call("account/read", {"refreshToken": False})
            if not account.get("account"):
                raise CodexAuthRequired("Connectez ce provider à votre compte ChatGPT avant de traduire.")
            instructions = "\n\n".join(
                m["content"] for m in body["messages"] if m["role"] in {"system", "developer"}
            )
            text = "\n\n".join(
                m["content"] for m in body["messages"] if m["role"] not in {"system", "developer"}
            )
            thread = await self.call(
                "thread/start",
                {
                    "model": body["model"],
                    "ephemeral": True,
                    "cwd": "/tmp",
                    "sandbox": "read-only",
                    "approvalPolicy": "untrusted",
                    "baseInstructions": instructions,
                    "developerInstructions": "This is text-only literary translation. Do not invoke tools, read files, execute commands or access the web. Return only the schema-conforming final response.",
                },
            )
            tid = thread["thread"]["id"]
            queue = asyncio.Queue(maxsize=256)
            self.events[tid] = queue
            turn_id = None
            finished = False
            try:
                async with asyncio.timeout(body["timeout"]):
                    args = {
                        "threadId": tid,
                        "input": [{"type": "text", "text": text, "text_elements": []}],
                        "outputSchema": body["schema"],
                        "approvalPolicy": "untrusted",
                        "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    }
                    if body.get("effort"):
                        args["effort"] = body["effort"]
                    turn = await self.call("turn/start", args)
                    turn_id = turn["turn"]["id"]
                    finals: dict[str, str] = {}
                    usage = {}
                    while True:
                        event = await queue.get()
                        params = event["params"]
                        if event["method"] == "disconnected":
                            raise CodexUnavailable("Codex s’est arrêté pendant la génération.")
                        if event["method"] == "thread/tokenUsage/updated":
                            last = params.get("tokenUsage", {}).get("last", {})
                            usage = {
                                "input_tokens": last.get("inputTokens", 0),
                                "output_tokens": last.get("outputTokens", 0),
                            }
                        elif event["method"] == "item/completed":
                            item = params.get("item", {})
                            if item.get("type") == "agentMessage" and item.get("phase") in {
                                None,
                                "final_answer",
                            }:
                                finals[item["id"]] = item.get("text", "")
                        elif event["method"] == "turn/completed":
                            if params.get("turn", {}).get("status") != "completed":
                                raise turn_error(params.get("turn", {}).get("error") or {})
                            final = list(finals.values())[-1] if finals else ""
                            if not final:
                                raise CodexError("Codex n’a pas renvoyé de réponse finale.")
                            finished = True
                            return {
                                "status": "completed",
                                "text": final,
                                "usage": usage,
                                "model": body["model"],
                                "thread_id": tid,
                                "turn_id": turn_id,
                            }
            finally:
                if not finished and not turn_id:
                    for _ in range(20):
                        turn_id = self.turn_ids.get(tid)
                        if turn_id:
                            break
                        await asyncio.sleep(0.05)
                if not finished and turn_id:
                    with contextlib.suppress(Exception):
                        await self.call("turn/interrupt", {"threadId": tid, "turnId": turn_id}, timeout=5)
                self.events.pop(tid, None)
                with contextlib.suppress(Exception):
                    await self.call("thread/unsubscribe", {"threadId": tid}, timeout=5)
                self.turn_ids.pop(tid, None)
                # A pause only interrupts this thread, never another book's app-server process.
