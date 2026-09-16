// PagentOS.Scripts.ColorCycler - one of the three FIXED catalogue scripts SceneDriver may
// attach (app.creative3d.spec.SCRIPT_CATALOGUE). sha256-pinned in drivers/manifest.json;
// never model-authored. Cycles its renderer's colour through the hue wheel while playing.
// Step(dt) is the whole behaviour, so SceneDriver's run_tests (req 533) steps exactly what
// the player runs; outside play mode it tints the shared material (the driver restores it)
// rather than leaking an instanced copy into the scene.

using UnityEngine;

namespace PagentOS.Scripts
{
    public sealed class ColorCycler : MonoBehaviour
    {
        public float secondsPerCycle = 4f;

        private float _elapsed;

        private void Update()
        {
            Step(Time.deltaTime);
        }

        public void Step(float dt)
        {
            var renderer = GetComponent<Renderer>();
            if (renderer == null)
            {
                return;
            }

            _elapsed += dt;
            var hue = Mathf.Repeat(_elapsed / Mathf.Max(secondsPerCycle, 0.1f), 1f);
            var material = Application.isPlaying ? renderer.material : renderer.sharedMaterial;
            if (material != null)
            {
                material.color = Color.HSVToRGB(hue, 0.8f, 1f);
            }
        }
    }
}
