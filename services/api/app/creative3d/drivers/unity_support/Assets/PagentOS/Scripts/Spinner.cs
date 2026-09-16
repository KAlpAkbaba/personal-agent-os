// PagentOS.Scripts.Spinner - one of the three FIXED catalogue scripts SceneDriver may attach
// (app.creative3d.spec.SCRIPT_CATALOGUE). sha256-pinned in drivers/manifest.json; never
// model-authored. Turns its object about the Y axis at a fixed rate while the scene plays.
// Step(dt) is the whole behaviour, so SceneDriver's run_tests (req 533) steps exactly what
// the player runs.

using UnityEngine;

namespace PagentOS.Scripts
{
    public sealed class Spinner : MonoBehaviour
    {
        public float degreesPerSecond = 45f;

        private void Update()
        {
            Step(Time.deltaTime);
        }

        public void Step(float dt)
        {
            transform.Rotate(0f, degreesPerSecond * dt, 0f, Space.World);
        }
    }
}
