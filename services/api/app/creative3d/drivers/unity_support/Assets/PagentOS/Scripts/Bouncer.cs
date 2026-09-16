// PagentOS.Scripts.Bouncer - one of the three FIXED catalogue scripts SceneDriver may attach
// (app.creative3d.spec.SCRIPT_CATALOGUE). sha256-pinned in drivers/manifest.json; never
// model-authored. Moves its object up and down around where it started. Step(dt) is the
// whole behaviour, so SceneDriver's run_tests (req 533) steps exactly what the player runs.

using UnityEngine;

namespace PagentOS.Scripts
{
    public sealed class Bouncer : MonoBehaviour
    {
        public float height = 0.5f;
        public float cyclesPerSecond = 0.5f;

        private Vector3 _origin;
        private bool _anchored;
        private float _elapsed;

        private void Start()
        {
            Anchor();
        }

        private void Update()
        {
            Step(Time.deltaTime);
        }

        public void Step(float dt)
        {
            if (!_anchored)
            {
                Anchor();
            }

            _elapsed += dt;
            var offset = Mathf.Abs(Mathf.Sin(_elapsed * cyclesPerSecond * Mathf.PI)) * height;
            transform.position = _origin + (Vector3.up * offset);
        }

        private void Anchor()
        {
            _origin = transform.position;
            _elapsed = 0f;
            _anchored = true;
        }
    }
}
