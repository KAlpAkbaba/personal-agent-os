using System.Runtime.CompilerServices;
using PagentOS.Agent.Core.Security;

namespace PagentOS.Agent.Tests.Support;

/// <summary>
/// A test run is a developer run.
///
/// Persisted machine material is protected for SYSTEM and Administrators only, which is right
/// for an installed service and would lock this non-elevated test process out of the files it
/// creates — the agent's own state, audit log and idempotency store. Declaring the posture
/// once, for the whole assembly, is the same thing a developer console run does through
/// PAGENTOS_AGENT_MachineMaterialMode=developer.
///
/// Tests that assert the PRODUCTION protection do not rely on this: they pass the posture
/// explicitly at the call, so what they assert cannot be changed by a global default.
/// </summary>
internal static class TestPosture
{
    [ModuleInitializer]
    internal static void DeclareDeveloperRun() => MachineMaterial.UseDeveloperPosture(true);
}
