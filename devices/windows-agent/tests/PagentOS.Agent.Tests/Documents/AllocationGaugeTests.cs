using PagentOS.Agent.Tests.Support;
using Xunit;
using Xunit.Abstractions;

namespace PagentOS.Agent.Tests.Documents;

/// <summary>
/// The gauge the M20 memory bounds rest on (ADR-0083 addendum 3): <c>BombFixtures.Measure</c>
/// reads process-wide counters, so what it reports has to be this work's own cost and not the
/// neighbouring collections'. On 2026-09-08 it was not: a bounded read failed about one run in
/// four on its 16 MiB bound because another test allocated inside its window, and the number in
/// the guarantee was raised to cover the noise. These tests hold the mechanism that let it be
/// put back — a window with nothing else running in it — and they fail if it is quietly lost.
/// </summary>
/// <remarks>
/// In the documents lab's collection, with the two classes whose bounds this gauge carries, so
/// that no two gauges ever measure at once: these tests read whether a quiet window was asked
/// for, and a neighbouring gauge asking for its own would be indistinguishable. The collection
/// still runs beside every other collection, which is the load they need to be measured under.
/// </remarks>
[Collection(DocumentLabCollection.Name)]
public sealed class AllocationGaugeTests(ITestOutputHelper output)
{
    /// <summary>Comfortably above what <see cref="AllocateOneMiB"/> costs, comfortably below what the neighbour throws at it.</summary>
    private const double BoundMiB = 8;

    /// <summary>A neighbour that allocated for longer than any gauge takes has gone wrong; it stops on its own.</summary>
    private static readonly TimeSpan NeighbourDeadline = TimeSpan.FromSeconds(30);

    [Fact]
    public void A_test_body_is_registered_in_the_activity_register_while_it_runs()
    {
        // If this fails, the assembly-level TestActivityRegistrar stopped being applied and every
        // "quiet window" below is a window that merely looks quiet: the register would read empty
        // whoever is running, the drain would return at once, and the memory bounds would go back
        // to measuring their neighbours. xunit takes BeforeAfterTestAttribute from the assembly as
        // well as the class and the method, and that is the only thing holding this up.
        Assert.True(TestActivity.Registered, "no test body has ever entered TestActivity; the assembly-level registrar is not being applied");
        Assert.True(TestActivity.InFlight >= 1, "this test body is running, so the register cannot be empty");

        // Applied to the ASSEMBLY, which is the part that cannot be checked by running: narrowed
        // to this class or this collection, everything above still passes — this test would enter
        // and leave itself — while every other collection went invisible to the register, so a
        // drain would find it empty at once and call a busy process quiet. Read the scope itself.
        Assert.Single(typeof(TestActivity).Assembly.GetCustomAttributes(typeof(TestActivityRegistrar), inherit: false));
    }

    [Fact]
    public void A_neighbour_allocating_hard_does_not_land_on_the_gauges_reading()
    {
        var started = new ManualResetEventSlim(false);
        var neighbour = new Neighbour(stopWhenQuietIsAsked: true, started);
        neighbour.Start();
        Assert.True(started.Wait(TimeSpan.FromSeconds(10)), "the neighbour never started allocating");

        // Every busy attempt is taken while the neighbour is hammering the process-wide counter,
        // so the gauge cannot answer from them: it has to ask for a quiet window and measure the
        // work again there. What comes back is the work's own cost.
        var (workingSetMiB, allocatedMiB) = BombFixtures.Measure(AllocateOneMiB, BoundMiB, output);

        Assert.True(neighbour.Join(TimeSpan.FromSeconds(30)), "the neighbour never stopped");
        output.WriteLine($"neighbour allocated {neighbour.AllocatedMiB} MiB while the gauge read allocated +{allocatedMiB:F1} MiB, working set +{workingSetMiB:F1} MiB for 1 MiB of work");

        Assert.True(neighbour.SawQuietRequest, "the gauge never asked for a quiet window: the noisy reading was taken as the answer");
        Assert.True(neighbour.AllocatedMiB > 4 * BoundMiB, $"the neighbour allocated only {neighbour.AllocatedMiB} MiB; that is not enough noise to prove anything");
        Assert.True(allocatedMiB < BoundMiB, $"the neighbour's {neighbour.AllocatedMiB} MiB landed on the gauge: it read {allocatedMiB:F1} MiB for work that allocates about 1");
    }

    [Fact]
    public void A_reading_already_under_the_bound_is_answered_without_closing_the_door()
    {
        // The counter this gauge reads can only be added to, so a busy reading under the bound is
        // already conclusive. Escalating anyway would stop the rest of the suite for every gauged
        // test in the assembly, so the fast path is part of the contract, not an optimisation.
        var started = new ManualResetEventSlim(false);
        var neighbour = new Neighbour(stopWhenQuietIsAsked: false, started);
        neighbour.Start();
        Assert.True(started.Wait(TimeSpan.FromSeconds(10)), "the neighbour never started");

        var (_, allocatedMiB) = BombFixtures.Measure(AllocateOneMiB, allocatedBoundMiB: 4096, output);

        neighbour.Stop();
        Assert.True(neighbour.Join(TimeSpan.FromSeconds(30)), "the neighbour never stopped");
        Assert.False(neighbour.SawQuietRequest, "the gauge closed the door for a reading that was already under its bound");
        Assert.True(allocatedMiB >= 1, $"the work allocates 1 MiB; the gauge read {allocatedMiB:F1}");
    }

    private static void AllocateOneMiB()
    {
        GC.KeepAlive(new byte[1024 * 1024]);
        // Long enough that a neighbour hammering the process-wide counter is certain to be seen
        // inside the window; short enough that six of these cost a tenth of a second.
        Thread.Sleep(25);
    }

    /// <summary>
    /// A test body that is not a test: it registers in <see cref="TestActivity"/> exactly as xunit
    /// would, and allocates hard, so the gauge meets in one thread what it otherwise only meets
    /// when the suite happens to schedule an expensive collection beside it.
    /// </summary>
    private sealed class Neighbour
    {
        private readonly bool _stopWhenQuietIsAsked;
        private readonly ManualResetEventSlim _started;
        private readonly Thread _thread;
        private volatile bool _stopped;
        private long _allocatedMiB;

        public Neighbour(bool stopWhenQuietIsAsked, ManualResetEventSlim started)
        {
            _stopWhenQuietIsAsked = stopWhenQuietIsAsked;
            _started = started;
            _thread = new Thread(Run) { IsBackground = true, Name = "allocation-gauge-neighbour" };
        }

        /// <summary>Whether the neighbour saw a gauge ask for a quiet window before it stopped.</summary>
        public bool SawQuietRequest { get; private set; }

        public long AllocatedMiB => Interlocked.Read(ref _allocatedMiB);

        public void Start() => _thread.Start();

        public void Stop() => _stopped = true;

        public bool Join(TimeSpan timeout) => _thread.Join(timeout);

        private void Run()
        {
            // Enter as a test body would, so the gauge's drain has something real to wait for.
            TestActivity.Enter();
            try
            {
                var deadline = DateTime.UtcNow + NeighbourDeadline;
                while (!_stopped && DateTime.UtcNow < deadline)
                {
                    if (TestActivity.QuietRequested)
                    {
                        SawQuietRequest = true;
                        if (_stopWhenQuietIsAsked)
                        {
                            return;
                        }
                    }

                    GC.KeepAlive(new byte[4 * 1024 * 1024]);
                    Interlocked.Add(ref _allocatedMiB, 4);
                    _started.Set();
                }
            }
            finally
            {
                // Leaving is what lets the gauge's drain finish; a neighbour that never left would
                // hold the window open until its timeout and the gauge would report an unverified
                // number rather than hang.
                TestActivity.Exit();
            }
        }
    }
}
