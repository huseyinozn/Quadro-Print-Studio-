/* Quadro Print Studio - Python worker (Pyodide). The page starts two of these:
   one plans and generates, the other only verifies (separate interpreter). */
// Binary parts (the Python runtime, its standard library and numpy) are published as base64
// text so every published file is reviewable text; they are decoded here, in the browser.
const B64 = { "pyodide.asm.wasm": ["pyodide/pyodide.asm.wasm.b64.txt", "application/wasm"],
              "python_stdlib.zip": ["pyodide/python_stdlib.zip.b64.txt", "application/zip"] };
const realFetch = self.fetch.bind(self);
async function decoded(path) {
  const t = (await (await realFetch(path)).text()).replace(/\s+/g, "");
  const bin = atob(t);
  const u = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
  return u;
}
self.fetch = async (input, init) => {
  const url = typeof input === "string" ? input : (input && input.url) || String(input);
  for (const [name, [path, type]] of Object.entries(B64)) {
    if (url.split("?")[0].endsWith("/" + name)) return new Response(await decoded(path), { headers: { "Content-Type": type } });
  }
  return realFetch(input, init);
};
importScripts("pyodide/pyodide.js");
const EXAMPLE_FILES = { "sharks-40": "sharks_single.gcode.txt", "orcacube": "orca_cube.gcode.txt", "fidgets-2c": "two_color_fidget.gcode.txt" };
let py = null, api = null;
const ready = (async () => {
  py = await loadPyodide({ indexURL: new URL("pyodide/", self.location).href });
  const whl = await decoded("pyodide/numpy-1.26.4.whl.b64.txt");
  py.unpackArchive(whl, "whl");                 // into site-packages
  py.runPython("import numpy");                   // fails loudly if the wheel did not load
  const bundle = await (await fetch("py_bundle.json")).json();
  for (const [p, src] of Object.entries(bundle)) {
    const parts = ("/lib/" + p).split("/"); parts.pop();
    let d = "";
    for (const seg of parts) { if (!seg) continue; d += "/" + seg; try { py.FS.mkdir(d); } catch (e) {} }
    py.FS.writeFile("/lib/" + p, src);
  }
  py.runPython("import sys; sys.path.insert(0, '/lib')");
  api = py.pyimport("quadro_studio.web.browser_api");
  for (const d of ["/in", "/v", "/work"]) { try { py.FS.mkdir(d); } catch (e) {} }
})();

function write(path, buf) { py.FS.writeFile(path, new Uint8Array(buf)); }

self.onmessage = async (e) => {
  const { id, cmd, a } = e.data;
  try {
    await ready;
    let result, transfer = [];
    if (cmd === "ping") result = true;
    else if (cmd === "examples") result = api.examples();
    else if (cmd === "plan") {
      if (a.example) {
        const r = await fetch("examples/" + EXAMPLE_FILES[a.example]);
        if (!r.ok) throw new Error("example file missing");
        write("/in/model.gcode", await r.arrayBuffer());
      } else write("/in/model.gcode", a.model);
      write("/in/profile.zip", a.profile);
      result = api.plan(a.mode, a.count, a.modelName || "model.gcode", "/in/model.gcode", "/in/profile.zip", a.example || "");
    } else if (cmd === "generate") {
      const r = JSON.parse(api.generate(a.jobId));
      if (r.error) throw new Error(r.error);
      const gcode = py.FS.readFile(r.folder + "/out.gcode");
      const model = py.FS.readFile(r.folder + "/model.gcode");
      const profile = py.FS.readFile(r.folder + "/profile.zip");
      result = { meta: r, gcode, model, profile };
      transfer = [gcode.buffer, model.buffer, profile.buffer];
    } else if (cmd === "verify") {
      write("/v/model.gcode", a.model); write("/v/profile.zip", a.profile); write("/v/out.gcode", a.gcode);
      result = api.verify(JSON.stringify(a.args));
    } else if (cmd === "zip") {
      const sha = api.zip_gcode(a.filename);
      const z = py.FS.readFile("/v/out.zip");
      result = { sha, zip: z }; transfer = [z.buffer];
    } else throw new Error("unknown command " + cmd);
    self.postMessage({ id, ok: true, result }, transfer);
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err && err.message || err) });
  }
};
