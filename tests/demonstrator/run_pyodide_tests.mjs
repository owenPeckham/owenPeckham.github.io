// Run the demonstrator's Python unit tests inside real Pyodide (Node.js).
//
// The runtime normally runs in a browser Web Worker with Pyodide's own
// matplotlib build, which can differ from your desktop matplotlib.  This
// runner executes tests/demonstrator/test_*.py in that exact environment.
//
// Usage (from the repository root):
//   npm install pyodide@0.29.5            # once; gives node_modules/pyodide
//   node tests/demonstrator/run_pyodide_tests.mjs
//
// Pyodide fetches numpy/matplotlib from the CDN on first use.  To use a local
// copy of the distribution instead (offline), point PYODIDE_DIR at a folder
// with pyodide.mjs, the lock file and the wheels:
//   PYODIDE_DIR=/path/to/pyodide/v0.29.5/full node tests/demonstrator/run_pyodide_tests.mjs
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "..", "..");
const dir = process.env.PYODIDE_DIR || path.join(repo, "node_modules", "pyodide");
const { loadPyodide } = await import(pathToFileURL(path.join(dir, "pyodide.mjs")).href);

const t0 = Date.now();
const py = await loadPyodide({ indexURL: dir + path.sep });
await py.loadPackage(["numpy", "matplotlib"], { messageCallback: () => {} });
console.log(`Pyodide ${py.version} ready in ${Date.now() - t0} ms`);

py.FS.mkdirTree("/repo");
py.FS.mount(py.FS.filesystems.NODEFS, { root: repo }, "/repo");

const pattern = process.argv[2] || "test_*.py";
const code = `
import sys, unittest, io
sys.path.insert(0, "/repo/tests/demonstrator")
import matplotlib
print("matplotlib", matplotlib.__version__, "python", sys.version.split()[0])
suite = unittest.defaultTestLoader.discover("/repo/tests/demonstrator", pattern=${JSON.stringify(pattern)})
stream = io.StringIO()
result = unittest.TextTestRunner(stream=stream, verbosity=1).run(suite)
print(stream.getvalue()[-6000:])
(len(result.failures) + len(result.errors), result.testsRun)
`;
const [bad, ran] = py.runPython(code).toJs();
console.log(`${ran} tests, ${bad} failing`);
process.exit(bad ? 1 : 0);
