// PagentOS.SceneDriver (docs/M25_CREATIVE_3D_SPEC.md §3, ADR-0088 decision 2).
//
// A FIXED repository file, sha256-pinned in manifest.json and asserted by
// tests/unit/test_blender_driver.py. Copied into the fixture project's
// Assets/PagentOS/Editor/ folder at scaffold time (project.scaffold, spec §3) and
// invoked in batch mode:
//
//   Unity.exe -batchmode -quit -projectPath <root>
//             -executeMethod PagentOS.SceneDriver.Run
//             -planPath <plan.json> -outPath <out.json> -logFile <log>
//
// Reads a ScenePlan (app.creative3d.spec.ScenePlan.plan_json() - canonical JSON, never
// C# generated from prose), executes every operation through the Editor API
// (GameObject.CreatePrimitive, Transform, Renderer.sharedMaterial, Light, Camera,
// AddComponent for catalogued scripts only), saves the scene, renders a still with
// Camera.Render to a RenderTexture -> PNG when asked, and writes the SAME inspection
// shape the Blender driver writes (out.json): every object with type/location/
// rotation/scale/material colour, the camera, the lights, the render path + sha256 +
// bytes, and errors[] - which also carries the console's own compile errors, so a
// deliberately broken catalogued script surfaces here rather than as a silent no-op
// (docs/M25_CREATIVE_3D_SPEC.md §6).
//
// B50 (reqs 532, 533): run_tests steps every attached catalogue script through its own
// Step(float) and reports tests[]; build_player builds the saved scene as a Windows
// player (Build/<scene>.exe) and reports build{} with the editor's hash - the Cloud Core
// then reads the executable back through the device before counting it.
//
// No model-authored C#: attach_script names one of a FIXED catalogue of scripts this
// same Assets/PagentOS/Scripts/ folder ships (Spinner.cs, Bouncer.cs, ColorCycler.cs -
// app.creative3d.spec.SCRIPT_CATALOGUE) - AddComponent(Type.GetType(...)) resolves
// against that fixed set only, never a type name taken from the plan verbatim without
// checking it first.
//
// Requires the project to reference Newtonsoft.Json (com.unity.nuget.newtonsoft-json) -
// the fixture project's own package manifest is the windows-engineer track's
// responsibility (ADR-0088 decision 8); this file assumes it is present, the same way
// the Blender driver assumes bpy is present.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace PagentOS
{
    /// <summary>
    /// The Unity half of the M25 closed loop. Every public entry point is static and
    /// argument-free (-executeMethod calls it with no arguments): everything it needs
    /// comes from Unity's own -planPath/-outPath command-line switches, read through
    /// System.Environment.GetCommandLineArgs() the same way the Blender driver reads
    /// its own "--" tail.
    /// </summary>
    public static class SceneDriver
    {
        private const int MaxOutJsonBytes = 256 * 1024;

        private static readonly Dictionary<string, string> ScriptCatalogue =
            new Dictionary<string, string>
            {
                { "Spinner", "PagentOS.Scripts.Spinner" },
                { "Bouncer", "PagentOS.Scripts.Bouncer" },
                { "ColorCycler", "PagentOS.Scripts.ColorCycler" },
            };

        // B50 (reqs 532, 533): what build_player and run_tests found, for the inspection.
        // Null when the plan did not ask; reset at the start of every run.
        private static string _sceneSlug = "scene";
        private static JObject _buildInfo;
        private static JArray _testResults;

        public static void Run()
        {
            var errors = new List<string>();
            string planPath = ReadArg("-planPath");
            string outPath = ReadArg("-outPath");
            JObject renderInfo = null;
            _buildInfo = null;
            _testResults = null;

            try
            {
                string planText = File.ReadAllText(planPath);
                JObject plan = JObject.Parse(planText);
                string sceneSlug = (string)plan["scene"] ?? "scene";
                _sceneSlug = sceneSlug;
                string outDir = Path.GetDirectoryName(Path.GetFullPath(outPath));

                foreach (var compileError in CollectCompileErrors())
                {
                    errors.Add(compileError);
                }

                var operations = (JArray)plan["operations"] ?? new JArray();
                foreach (JObject op in operations.OfType<JObject>())
                {
                    ApplyOperation(op, outDir, errors, ref renderInfo);
                }

                SaveScene(outDir, sceneSlug, errors);
                JObject inspection = BuildInspection(errors, renderInfo);
                WriteOutJson(outPath, inspection);
            }
            catch (Exception exc)
            {
                errors.Add("driver: " + exc.Message);
                var inspection = BuildInspection(errors, renderInfo);
                WriteOutJson(outPath, inspection);
            }
            finally
            {
                EditorApplication.Exit(0);
            }
        }

        private static string ReadArg(string name)
        {
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length - 1; i++)
            {
                if (args[i] == name)
                {
                    return args[i + 1];
                }
            }
            throw new ArgumentException("missing command-line argument " + name);
        }

        /// <summary>Console compile errors (spec §6: "a deliberately broken catalogued
        /// script -> errors[] non-empty and the receipt says so") - read from the
        /// Editor's own log entries rather than re-implemented, so a script that fails
        /// to compile is reported truthfully instead of silently skipped.</summary>
        private static IEnumerable<string> CollectCompileErrors()
        {
            var messages = new List<string>();
            if (EditorUtility.scriptCompilationFailed)
            {
                messages.Add("compile: one or more scripts failed to compile");
            }
            return messages;
        }

        private static void ApplyOperation(
            JObject op, string outDir, List<string> errors, ref JObject renderInfo)
        {
            string kind = (string)op["op"];
            try
            {
                switch (kind)
                {
                    case "create_scene":
                        ClearScene();
                        break;
                    case "add_primitive":
                        AddPrimitive(op, errors);
                        break;
                    case "transform":
                        ApplyTransform(op, errors);
                        break;
                    case "set_material":
                        ApplyMaterial(op, errors);
                        break;
                    case "set_camera":
                        ApplyCamera(op, errors);
                        break;
                    case "set_light":
                        ApplyLight(op, errors);
                        break;
                    case "attach_script":
                        AttachScript(op, errors);
                        break;
                    case "render":
                        renderInfo = DoRender(op, outDir, errors) ?? renderInfo;
                        break;
                    case "run_tests":
                        _testResults = RunTests(op, errors);
                        break;
                    case "build_player":
                        _buildInfo = BuildPlayer(outDir, errors);
                        break;
                    case "inspect":
                        break;
                    default:
                        errors.Add("unknown operation '" + kind + "'");
                        break;
                }
            }
            catch (Exception exc)
            {
                errors.Add(kind + ": " + exc.Message);
            }
        }

        private static void ClearScene()
        {
            Scene scene = SceneManager.GetActiveScene();
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                UnityEngine.Object.DestroyImmediate(root);
            }
        }

        /// <summary>A plan field the owner set: present and not JSON null. ScenePlan.plan_json()
        /// writes every optional field, unset ones as null, so a bare `!= null` test was true for
        /// all of them and the casts below threw (B50, the first licensed run).</summary>
        private static bool IsSet(JToken token)
        {
            return token != null && token.Type != JTokenType.Null;
        }

        private static Vector3 ReadVec3(JToken token, Vector3 fallback)
        {
            if (!IsSet(token)) return fallback;
            var arr = (JArray)token;
            return new Vector3((float)arr[0], (float)arr[1], (float)arr[2]);
        }

        private static void AddPrimitive(JObject op, List<string> errors)
        {
            string kind = (string)op["kind"];
            string name = (string)op["name"];
            Vector3 location = ReadVec3(op["location"], Vector3.zero);
            Vector3 rotation = ReadVec3(op["rotation"], Vector3.zero);
            Vector3 scale = ReadVec3(op["scale"], Vector3.one);

            GameObject go;
            switch (kind)
            {
                case "cube":
                    go = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    break;
                case "sphere":
                    go = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                    break;
                case "cylinder":
                    go = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
                    break;
                case "plane":
                    go = GameObject.CreatePrimitive(PrimitiveType.Plane);
                    break;
                case "light_sun":
                    go = new GameObject(name);
                    var sun = go.AddComponent<Light>();
                    sun.type = LightType.Directional;
                    break;
                case "light_point":
                    go = new GameObject(name);
                    var point = go.AddComponent<Light>();
                    point.type = LightType.Point;
                    break;
                case "camera":
                    go = new GameObject(name);
                    go.AddComponent<Camera>();
                    break;
                default:
                    errors.Add("add_primitive: unknown kind '" + kind + "'");
                    return;
            }
            go.name = name;
            go.transform.position = location;
            go.transform.eulerAngles = rotation;
            go.transform.localScale = scale;
        }

        private static void ApplyTransform(JObject op, List<string> errors)
        {
            string name = (string)op["name"];
            GameObject go = GameObject.Find(name);
            if (go == null)
            {
                errors.Add("transform: object '" + name + "' not found");
                return;
            }
            if (IsSet(op["location"])) go.transform.position = ReadVec3(op["location"], Vector3.zero);
            if (IsSet(op["rotation"])) go.transform.eulerAngles = ReadVec3(op["rotation"], Vector3.zero);
            if (IsSet(op["scale"])) go.transform.localScale = ReadVec3(op["scale"], Vector3.one);
        }

        private static void ApplyMaterial(JObject op, List<string> errors)
        {
            string name = (string)op["name"];
            GameObject go = GameObject.Find(name);
            Renderer renderer = go != null ? go.GetComponent<Renderer>() : null;
            if (renderer == null)
            {
                errors.Add("set_material: object '" + name + "' cannot carry a material");
                return;
            }
            var color = (JArray)op["color"];
            var material = new Material(Shader.Find("Standard"));
            material.color = new Color((float)color[0], (float)color[1], (float)color[2], (float)color[3]);
            if (IsSet(op["metallic"])) material.SetFloat("_Metallic", (float)op["metallic"]);
            if (IsSet(op["roughness"])) material.SetFloat("_Glossiness", 1f - (float)op["roughness"]);
            renderer.sharedMaterial = material;
        }

        private static void ApplyCamera(JObject op, List<string> errors)
        {
            string name = (string)op["name"];
            GameObject go = GameObject.Find(name);
            Camera camera = go != null ? go.GetComponent<Camera>() : null;
            if (camera == null)
            {
                errors.Add("set_camera: object '" + name + "' not found");
                return;
            }
            camera.tag = "MainCamera";
            string lookAt = (string)op["look_at"];
            if (lookAt != null)
            {
                GameObject target = GameObject.Find(lookAt);
                if (target == null)
                {
                    errors.Add("set_camera: look_at target '" + lookAt + "' not found");
                    return;
                }
                go.transform.LookAt(target.transform);
            }
        }

        private static void ApplyLight(JObject op, List<string> errors)
        {
            string name = (string)op["name"];
            GameObject go = GameObject.Find(name);
            Light light = go != null ? go.GetComponent<Light>() : null;
            if (light == null)
            {
                errors.Add("set_light: light '" + name + "' not found");
                return;
            }
            light.intensity = (float)op["energy"];
        }

        private static void AttachScript(JObject op, List<string> errors)
        {
            string name = (string)op["name"];
            string scriptId = (string)op["script_id"];
            GameObject go = GameObject.Find(name);
            if (go == null)
            {
                errors.Add("attach_script: object '" + name + "' not found");
                return;
            }
            if (!ScriptCatalogue.TryGetValue(scriptId, out string typeName))
            {
                // ScenePlan already refuses a script_id outside the catalogue before
                // this ever runs; kept as defence in depth against a tampered plan.
                errors.Add("attach_script: script_id '" + scriptId + "' is not catalogued");
                return;
            }
            // The catalogue scripts compile into the project's runtime assembly, not into this
            // editor assembly, so Type.GetType(name) alone never finds them: look in every
            // loaded assembly for exactly the catalogued full name (nothing from the plan).
            Type type = Type.GetType(typeName)
                ?? AppDomain.CurrentDomain.GetAssemblies()
                    .Select(assembly => assembly.GetType(typeName, throwOnError: false))
                    .FirstOrDefault(candidate => candidate != null && typeof(MonoBehaviour).IsAssignableFrom(candidate));
            if (type == null)
            {
                errors.Add("attach_script: catalogued script '" + scriptId + "' failed to resolve "
                    + "(compile error?)");
                return;
            }
            go.AddComponent(type);
        }

        private static JObject DoRender(JObject op, string outDir, List<string> errors)
        {
            int width = op["width"] != null ? (int)op["width"] : 320;
            int height = op["height"] != null ? (int)op["height"] : 240;
            Camera camera = Camera.main;
            if (camera == null)
            {
                errors.Add("render: no camera in the scene");
                return null;
            }
            if (SystemInfo.graphicsDeviceType == UnityEngine.Rendering.GraphicsDeviceType.Null)
            {
                // B50: under -nographics Camera.Render draws one flat colour; say so rather
                // than hand back a PNG that only looks like a render.
                errors.Add("render: no graphics device (the editor ran with -nographics)");
                return null;
            }
            var rt = new RenderTexture(width, height, 24);
            RenderTexture previous = camera.targetTexture;
            camera.targetTexture = rt;
            var tex = new Texture2D(width, height, TextureFormat.RGB24, false);
            camera.Render();
            RenderTexture.active = rt;
            tex.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            tex.Apply();
            camera.targetTexture = previous;
            RenderTexture.active = null;
            UnityEngine.Object.DestroyImmediate(rt);

            string renderPath = Path.Combine(outDir, "render.png");
            byte[] png = tex.EncodeToPNG();
            File.WriteAllBytes(renderPath, png);
            UnityEngine.Object.DestroyImmediate(tex);

            string sha256 = Sha256Hex(png);
            var info = new JObject
            {
                // Relative to the project (the folder out.json is written in): the device's
                // scene.inspect refuses an absolute render path. The Blender driver was fixed
                // for the same refusal in B44; this driver still sent the absolute one until the
                // first licensed Unity run (B50, ADR-0164).
                ["path"] = Path.GetFileName(renderPath),
                ["sha256"] = sha256,
                ["bytes"] = png.Length,
                ["width"] = width,
                ["height"] = height,
                ["engine"] = "unity",
            };
            return info;
        }

        private static string Sha256Hex(byte[] data)
        {
            using (var sha = SHA256.Create())
            {
                byte[] hash = sha.ComputeHash(data);
                return string.Concat(hash.Select(b => b.ToString("x2")));
            }
        }

        /// <summary>req 533: every catalogued script attached in the scene, stepped
        /// <c>frames</c> times through its own <c>Step(float)</c> - the method its Update
        /// calls in the player - and required to have acted. The object is restored after,
        /// so the saved scene (and the player built from it) is the plan's scene.</summary>
        private static JArray RunTests(JObject op, List<string> errors)
        {
            int frames = IsSet(op["frames"]) ? (int)op["frames"] : 30;
            const float dt = 1f / 30f;
            var results = new JArray();
            Scene scene = SceneManager.GetActiveScene();
            var behaviours = scene.GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<MonoBehaviour>(true))
                .Where(b => b != null && ScriptCatalogue.ContainsValue(b.GetType().FullName))
                .ToList();
            foreach (MonoBehaviour behaviour in behaviours)
            {
                GameObject go = behaviour.gameObject;
                string scriptId = ScriptCatalogue.First(p => p.Value == behaviour.GetType().FullName).Key;
                var step = behaviour.GetType().GetMethod("Step", new[] { typeof(float) });
                Renderer renderer = go.GetComponent<Renderer>();
                Material material = renderer != null ? renderer.sharedMaterial : null;
                Vector3 position = go.transform.position;
                Quaternion rotation = go.transform.rotation;
                Color color = material != null ? material.color : Color.clear;
                bool passed = false;
                string detail;
                if (step == null)
                {
                    detail = "no Step(float) method";
                }
                else
                {
                    try
                    {
                        for (int i = 0; i < frames; i++)
                        {
                            step.Invoke(behaviour, new object[] { dt });
                        }

                        switch (scriptId)
                        {
                            case "Spinner":
                                float turned = Quaternion.Angle(rotation, go.transform.rotation);
                                passed = turned > 1f;
                                detail = "turned " + turned.ToString("0.###", CultureInfo.InvariantCulture) + " degrees";
                                break;
                            case "Bouncer":
                                float moved = Vector3.Distance(position, go.transform.position);
                                passed = moved > 0.01f;
                                detail = "moved " + moved.ToString("0.###", CultureInfo.InvariantCulture);
                                break;
                            default:
                                Color now = material != null ? material.color : Color.clear;
                                float shift = Mathf.Abs(now.r - color.r) + Mathf.Abs(now.g - color.g) + Mathf.Abs(now.b - color.b);
                                passed = material != null && shift > 0.05f;
                                detail = material != null ? "colour shifted " + shift.ToString("0.###", CultureInfo.InvariantCulture) : "no material to cycle";
                                break;
                        }
                    }
                    catch (Exception exc)
                    {
                        detail = "threw: " + (exc.InnerException ?? exc).Message;
                    }
                    finally
                    {
                        go.transform.position = position;
                        go.transform.rotation = rotation;
                        if (material != null)
                        {
                            material.color = color;
                        }
                    }
                }

                results.Add(new JObject
                {
                    ["object"] = go.name,
                    ["script"] = scriptId,
                    ["frames"] = frames,
                    ["passed"] = passed,
                    ["detail"] = detail,
                });
            }

            if (results.Count == 0)
            {
                errors.Add("run_tests: no catalogued script is attached in this scene");
            }
            return results;
        }

        /// <summary>req 532: the saved scene built by Unity's own BuildPipeline as a
        /// Windows player at <c>Build/&lt;scene&gt;.exe</c> inside the project. The editor's
        /// hash is declared here; the Cloud Core counts the build only after the device
        /// reads the same bytes back as a PE (file.inspect).</summary>
        private static JObject BuildPlayer(string outDir, List<string> errors)
        {
            string scenePath = SaveScene(outDir, _sceneSlug, errors);
            string relative = "Build/" + _sceneSlug + ".exe";
            string location = Path.Combine(outDir, "Build", _sceneSlug + ".exe");
            var options = new BuildPlayerOptions
            {
                scenes = new[] { scenePath },
                locationPathName = location,
                target = BuildTarget.StandaloneWindows64,
                targetGroup = BuildTargetGroup.Standalone,
                options = BuildOptions.None,
            };
            BuildReport report = BuildPipeline.BuildPlayer(options);
            BuildSummary summary = report.summary;
            var info = new JObject
            {
                ["target"] = "windows64",
                ["result"] = summary.result.ToString(),
                ["path"] = relative,
                ["build_errors"] = summary.totalErrors,
            };
            if (summary.result == BuildResult.Succeeded && File.Exists(location))
            {
                byte[] bytes = File.ReadAllBytes(location);
                info["sha256"] = Sha256Hex(bytes);
                info["bytes"] = bytes.Length;
            }
            else
            {
                errors.Add("build_player: " + summary.result + " (" + summary.totalErrors + " errors)");
            }
            return info;
        }

        private static string SaveScene(string outDir, string sceneSlug, List<string> errors)
        {
            string scenePath = "Assets/" + sceneSlug + ".unity";
            try
            {
                Scene scene = SceneManager.GetActiveScene();
                EditorSceneManager.SaveScene(scene, scenePath);
            }
            catch (Exception exc)
            {
                errors.Add("save_scene: " + exc.Message);
            }
            return scenePath;
        }

        private static JObject BuildInspection(List<string> errors, JObject renderInfo)
        {
            var objects = new JArray();
            string cameraName = null;
            var lights = new JArray();
            Scene scene = SceneManager.GetActiveScene();
            foreach (GameObject root in scene.GetRootGameObjects())
            {
                foreach (Transform t in root.GetComponentsInChildren<Transform>(true))
                {
                    GameObject go = t.gameObject;
                    var entry = new JObject
                    {
                        ["name"] = go.name,
                        ["type"] = ClassifyType(go),
                        ["location"] = Vec3Json(go.transform.position),
                        ["rotation"] = Vec3Json(go.transform.eulerAngles),
                        ["scale"] = Vec3Json(go.transform.localScale),
                    };
                    Renderer renderer = go.GetComponent<Renderer>();
                    if (renderer != null && renderer.sharedMaterial != null)
                    {
                        Color c = renderer.sharedMaterial.color;
                        entry["material_color"] = new JArray(c.r, c.g, c.b, c.a);
                    }
                    objects.Add(entry);
                    if (go.GetComponent<Camera>() != null) cameraName = go.name;
                    Light light = go.GetComponent<Light>();
                    if (light != null)
                    {
                        lights.Add(new JObject { ["name"] = go.name, ["energy"] = light.intensity });
                    }
                }
            }
            return new JObject
            {
                ["objects"] = objects,
                ["camera"] = cameraName,
                ["lights"] = lights,
                ["render"] = renderInfo,
                ["build"] = _buildInfo,
                ["tests"] = _testResults,
                ["errors"] = new JArray(errors),
            };
        }

        private static string ClassifyType(GameObject go)
        {
            if (go.GetComponent<Camera>() != null) return "CAMERA";
            if (go.GetComponent<Light>() != null) return "LIGHT";
            if (go.GetComponent<MeshFilter>() != null) return "MESH";
            return "EMPTY";
        }

        private static JArray Vec3Json(Vector3 v) => new JArray(v.x, v.y, v.z);

        private static void WriteOutJson(string outPath, JObject inspection)
        {
            string text = inspection.ToString(Newtonsoft.Json.Formatting.None);
            if (System.Text.Encoding.UTF8.GetByteCount(text) > MaxOutJsonBytes)
            {
                var errors = (JArray)inspection["errors"] ?? new JArray();
                errors.Add("inspection truncated: exceeded the size bound");
                inspection["errors"] = errors;
                var objects = (JArray)inspection["objects"];
                if (objects != null && objects.Count > 50)
                {
                    inspection["objects"] = new JArray(objects.Take(50));
                }
                text = inspection.ToString(Newtonsoft.Json.Formatting.None);
            }
            File.WriteAllText(outPath, text);
        }
    }
}
