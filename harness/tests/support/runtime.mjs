import { spawn } from "node:child_process";
import { EventEmitter } from "node:events";
export class RuntimeClient extends EventEmitter {
    maxFrame = 1024 * 1024;
    child;
    sequence = 0;
    pending = new Map();
    buffer = Buffer.alloc(0);
    closed = false;
    constructor(binary, args) {
        super();
        const env = {};
        for (const key of [
            "PATH",
            "SystemRoot",
            "WINDIR",
            "USERPROFILE",
            "LOCALAPPDATA",
            "TEMP",
            "TMP",
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_RUNTIME_DIR",
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_CONFIG",
            "OPENSHELL_GATEWAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "LANG",
        ])
            if (process.env[key])
                env[key] = process.env[key];
        this.child = spawn(binary, args, {
            stdio: ["pipe", "pipe", "pipe"],
            env,
            windowsHide: true,
        });
        this.child.stdout.on("data", (chunk) => {
            if (this.closed)
                return;
            this.buffer = Buffer.concat([this.buffer, chunk]);
            let n;
            while ((n = this.buffer.indexOf(10)) >= 0) {
                if (n > this.maxFrame)
                    return this.fail(Error("Runtime frame limit exceeded; reconcile the saved session."));
                const raw = this.buffer.subarray(0, n);
                this.buffer = this.buffer.subarray(n + 1);
                try {
                    this.receive(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(raw)));
                }
                catch {
                    return this.fail(Error("Invalid runtime protocol frame"));
                }
            }
            if (this.buffer.length > this.maxFrame)
                this.fail(Error("Runtime frame limit exceeded; reconcile the saved session."));
        });
        // Drain diagnostics without copying possible provider/tool details into UI logs.
        this.child.stderr.on("data", () => { });
        this.child.stdin.on("error", () => this.fail(Error("Runtime input disconnected. Reattach before retrying.")));
        this.child.on("error", () => this.fail(Error("Could not launch the native runtime. Check OPENWORKER_BIN.")));
        this.child.on("exit", () => this.fail(Error("Runtime disconnected. Reconnect to restore its saved session.")));
    }
    receive(message) {
        if (!message || message.jsonrpc !== "2.0")
            throw Error("Invalid protocol version");
        const p = this.pending.get(message.id);
        if (p) {
            if (Object.hasOwn(message, "result") === Object.hasOwn(message, "error"))
                throw Error("Invalid response");
            clearTimeout(p.timer);
            this.pending.delete(message.id);
            message.error
                ? p.reject(Error("Harness rejected the request. Check settings and saved session state."))
                : p.resolve(message.result);
        }
        else if (message.method === "session/event")
            this.emit("event", message.params);
        else if (message.method === "stream/gap")
            this.emit("gap");
    }
    fail(error) {
        if (this.closed)
            return;
        this.closed = true;
        if (this.child.exitCode === null && this.child.signalCode === null)
            this.child.kill();
        for (const p of this.pending.values()) {
            clearTimeout(p.timer);
            p.reject(error);
        }
        this.pending.clear();
        this.emit("disconnected", error.message);
    }
    get alive() {
        return !this.closed;
    }
    request(method, params) {
        if (this.closed)
            return Promise.reject(Error("Runtime is disconnected"));
        if (this.pending.size >= 64)
            return Promise.reject(Error("Runtime request queue is full; request not sent."));
        const id = ++this.sequence;
        const frame = JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
        if (Buffer.byteLength(frame) > this.maxFrame)
            return Promise.reject(Error("Runtime request exceeds frame limit; request not sent."));
        return new Promise((resolve, reject) => {
            const timer = setTimeout(() => {
                this.fail(Error("Request outcome unknown after timeout. Reattach before retrying any mutation."));
            }, 180000);
            this.pending.set(id, { resolve, reject, timer });
            this.child.stdin.write(frame);
        });
    }
    async stop() {
        if (this.closed)
            return;
        await new Promise((resolve) => {
            const t = setTimeout(() => {
                this.child.kill("SIGKILL");
                resolve();
            }, 20000);
            this.child.once("exit", () => {
                clearTimeout(t);
                resolve();
            });
            this.child.stdin.end();
        });
    }
}
