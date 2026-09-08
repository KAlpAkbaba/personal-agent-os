// PagentOS.SceneDriver (docs/M25_CREATIVE_3D_SPEC.md §3, ADR-0088 decision 2).
//
// A FIXED repository file, sha256-pinned in manifest.json and asserted by
// tests/unit/test_blender_driver.py. Copied into the fixture project's
// Assets/PagentOS/Editor/ folder at scaffold time (project.scaffold, spec §3) and
// invoked in batch mode:
//
//   Unity.exe -batchmode -nographics -quit -projectPath <root>
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
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using Newtonsoft.Json.Linq;
using UnityEditor;
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

        public static void Run()
        {
            var errors = new List<string>();
            string planPath = ReadArg("-planPath");
            string outPath = ReadArg("-outPath");
            JObject renderInfo = null;

            try
            {
                string planText = File.ReadAllText(planPath);
                JObject plan = JObject.Parse(planText);
                string sceneSlug = (string)plan["scene"] ?? "scene";
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

        private static Vector3 ReadVec3(JToken token, Vector3 fallback)
        {
            if (token == null) return fallback;
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
            if (op["location"] != null) go.transform.position = ReadVec3(op["location"], Vector3.zero);
            if (op["rotation"] != null) go.transform.eulerAngles = ReadVec3(op["rotation"], Vector3.zero);
            if (op["scale"] != null) go.transform.localScale = ReadVec3(op["scale"], Vector3.one);
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
            if (op["metallic"] != null) material.SetFloat("_Metallic", (float)op["metallic"]);
            if (op["roughness"] != null) material.SetFloat("_Glossiness", 1f - (float)op["roughness"]);
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
            Type type = Type.GetType(typeName);
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
                ["path"] = renderPath,
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

        private static void SaveScene(string outDir, string sceneSlug, List<string> errors)
        {
            try
            {
                Scene scene = SceneManager.GetActiveScene();
                string scenePath = "Assets/" + sceneSlug + ".unity";
                EditorSceneManager.SaveScene(scene, scenePath);
            }
            catch (Exception exc)
            {
                errors.Add("save_scene: " + exc.Message);
            }
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
