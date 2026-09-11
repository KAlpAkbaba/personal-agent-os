namespace PagentOS.Agent.Tests.Support;

/// <summary>
/// The assembly's register of running test bodies, and the quiet window the memory gauges
/// measure in.
///
/// <para>
/// The memory gauges in <c>BombFixtures</c> read <see cref="GC.GetTotalAllocatedBytes"/> and
/// <c>Process.WorkingSet64</c>. Both are PROCESS-wide, and xunit runs collections in parallel,
/// so a neighbour's allocation lands on the reading of whatever is being measured. The work
/// itself cannot be measured on its own thread: the capability runs its dispatch on a pool
/// thread (<c>DocumentCapabilities.ExecuteAsync</c> hands the read-only names to
/// <c>Task.Run</c>), so the per-thread counter would see almost none of it and would report a
/// bound as kept when it was broken. The measurement has to be made in a window where nothing
/// else is running instead — which is what this register provides.
/// </para>
///
/// <para>
/// Every test body enters here through <see cref="TestActivityRegistrar"/>, an assembly-level
/// <c>BeforeAfterTestAttribute</c>. A gauge asks for a quiet window: new tests wait at the
/// door, the tests already running are allowed to finish, and the measurement is taken with
/// <see cref="InFlight"/> at one — the measuring test itself. <see cref="Epoch"/> is bumped on
/// every entry and exit, so the gauge can prove after the fact that its window really was
/// undisturbed rather than assume it.
/// </para>
///
/// <para>
/// Every wait here is bounded. A quiet window is a measurement aid, never a lock the suite can
/// hang on: if the drain does not come in time the gauge is told so and reports an unverified
/// number, and a test blocked at the door gives up waiting and runs.
/// </para>
///
/// <para>
/// What a verified window claims, exactly: no other test BODY ran in it. xunit builds a test
/// class before <c>Before</c> and disposes it after <c>After</c>, so a neighbouring class's
/// constructor or teardown is outside the register and can still allocate inside the window.
/// That error is one-sided — it can only make a reading look worse — so it costs a gauge an
/// extra round at worst and can never turn a broken bound into a passing one.
/// </para>
/// </summary>
public static class TestActivity
{
    /// <summary>
    /// How long a gauge waits for the tests already running to finish before it measures anyway.
    /// The door is shut for the whole wait, so this is time the rest of the suite spends standing
    /// still: long enough for the short tests to clear out, short enough that one test still
    /// inside its own ten-second wait does not stall the run behind it.
    /// </summary>
    public static readonly TimeSpan DefaultDrainTimeout = TimeSpan.FromSeconds(5);

    /// <summary>The safety valve: a test never waits at the door longer than this, whatever the gauge is doing.</summary>
    public static readonly TimeSpan DoorTimeout = TimeSpan.FromSeconds(60);

    private static readonly object Gate = new();
    private static int _inFlight;
    private static long _epoch;
    private static bool _quietRequested;
    private static bool _everEntered;

    /// <summary>How many test bodies are running right now, the caller's own included.</summary>
    public static int InFlight
    {
        get { lock (Gate) { return _inFlight; } }
    }

    /// <summary>
    /// Bumped on every entry and exit. Two equal readings across a window mean no test started
    /// or finished inside it.
    /// </summary>
    public static long Epoch
    {
        get { lock (Gate) { return _epoch; } }
    }

    /// <summary>True while a gauge is waiting for, or holding, a quiet window.</summary>
    public static bool QuietRequested
    {
        get { lock (Gate) { return _quietRequested; } }
    }

    /// <summary>A test body is starting. Waits at the door while a gauge is measuring.</summary>
    /// <param name="byRegistrar">
    /// Set only by <see cref="TestActivityRegistrar"/>, and the only thing that can raise
    /// <see cref="Registered"/>. A test double standing in for a test body enters here too, so
    /// counting its entry as proof the registrar is applied would let the wiring guard pass on
    /// its own stunt double.
    /// </param>
    public static void Enter(bool byRegistrar = false)
    {
        lock (Gate)
        {
            var deadline = DateTime.UtcNow + DoorTimeout;
            while (_quietRequested)
            {
                var remaining = deadline - DateTime.UtcNow;
                if (remaining <= TimeSpan.Zero || !Monitor.Wait(Gate, remaining))
                {
                    // The safety valve: something is holding the window open far longer than a
                    // measurement takes. Run rather than hang the suite on it.
                    break;
                }
            }

            _inFlight++;
            _epoch++;
            _everEntered |= byRegistrar;
        }
    }

    /// <summary>A test body has finished.</summary>
    public static void Exit()
    {
        lock (Gate)
        {
            _inFlight--;
            _epoch++;
            Monitor.PulseAll(Gate);
        }
    }

    /// <summary>
    /// What a measurement window turned out to be. <see cref="Verified"/> is the only claim worth
    /// resting a failure on: the door was shut, everyone else had left, and nobody entered or
    /// left while the work was measured.
    /// </summary>
    /// <param name="Drained">The register fell to the caller alone before the measurement began.</param>
    /// <param name="Undisturbed">No test entered or left during the measurement.</param>
    /// <param name="InFlightAtStart">How many test bodies were still running when the measurement began — one is the caller itself.</param>
    public readonly record struct QuietWindow(bool Drained, bool Undisturbed, int InFlightAtStart)
    {
        /// <summary>
        /// The register held exactly the caller — never zero, which would mean the caller's own
        /// test body was not counted and the emptiness proved nothing about the process.
        /// </summary>
        public bool Verified => Drained && Undisturbed && InFlightAtStart == 1;

        public override string ToString()
            => Verified
                ? "a verified quiet window"
                : $"a window that could NOT be verified quiet ({Reason})";

        private string Reason => !Drained
            ? $"{InFlightAtStart} test bodies were still running when the drain timed out"
            : InFlightAtStart == 1
                ? "the register moved during the measurement"
                : $"the register held {InFlightAtStart} test bodies, so the caller itself was not counted";
    }

    /// <summary>
    /// Run <paramref name="measure"/> with no other test body running, and say whether that was
    /// actually achieved. A window that is not <see cref="QuietWindow.Verified"/> still gives the
    /// caller a number — it is simply a number the caller may report but must not fail on.
    /// </summary>
    /// <remarks>
    /// The caller is expected to be a test body, and so to be counted in <see cref="InFlight"/>
    /// itself; the drain waits for the count to fall to that one caller.
    /// </remarks>
    public static (QuietWindow Window, T Value) Measure<T>(Func<T> measure, TimeSpan? drainTimeout = null)
    {
        var deadline = DateTime.UtcNow + (drainTimeout ?? DefaultDrainTimeout);
        var (held, drained) = RequestQuiet(deadline);
        try
        {
            int inFlight;
            long before;
            lock (Gate)
            {
                inFlight = _inFlight;
                before = _epoch;
            }

            var value = measure();
            return (new QuietWindow(drained, Epoch == before, inFlight), value);
        }
        finally
        {
            if (held)
            {
                lock (Gate)
                {
                    _quietRequested = false;
                    Monitor.PulseAll(Gate);
                }
            }
        }
    }

    /// <summary>
    /// Whether the assembly-level registrar has ever entered a test body. False only if the
    /// <see cref="TestActivityRegistrar"/> stopped being applied, which would turn every quiet
    /// window into a window that merely looks quiet — the register would read empty whoever was
    /// running and every drain would return at once. The gauge's own tests assert this rather
    /// than let the bounds go back to measuring their neighbours in silence.
    /// </summary>
    public static bool Registered
    {
        get { lock (Gate) { return _everEntered; } }
    }

    /// <summary>
    /// Close the door and wait for the tests already inside to leave. <c>Held</c> says whether the
    /// door is ours to open again — a caller that never got it must not clear another gauge's
    /// window on its way out. A <paramref name="deadline"/> that has already passed asks for the
    /// door and nothing more: no new test starts, and the measurement is taken beside whoever is
    /// still inside, which is most of the quiet a short window needs.
    /// </summary>
    private static (bool Held, bool Drained) RequestQuiet(DateTime deadline)
    {
        lock (Gate)
        {
            // One gauge at a time: a second one waits for the first to finish rather than
            // waiting for a drain that can never come while the first is itself in flight.
            while (_quietRequested)
            {
                var remaining = deadline - DateTime.UtcNow;
                if (remaining <= TimeSpan.Zero || !Monitor.Wait(Gate, remaining))
                {
                    return (false, false);
                }
            }

            _quietRequested = true;

            // The caller is one of the tests in flight, so one is as empty as the register gets.
            while (_inFlight > 1)
            {
                var remaining = deadline - DateTime.UtcNow;
                if (remaining <= TimeSpan.Zero || !Monitor.Wait(Gate, remaining))
                {
                    return (true, false);
                }
            }

            return (true, true);
        }
    }
}
