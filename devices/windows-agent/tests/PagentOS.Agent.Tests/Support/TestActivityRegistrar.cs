using System.Reflection;
using PagentOS.Agent.Tests.Support;
using Xunit.Sdk;

// Applied to the whole assembly: xunit collects BeforeAfterTestAttribute from the assembly, the
// collection definition, the class and the method, so one declaration here registers every test
// body in the run. Without it the memory gauges cannot tell a quiet window from a busy one.
[assembly: TestActivityRegistrar]

namespace PagentOS.Agent.Tests.Support;

/// <summary>
/// Registers every test body in <see cref="TestActivity"/> for as long as it runs, so a memory
/// gauge can wait for a window with nothing else in it. Entering costs one uncontended lock per
/// test and blocks only while a gauge is actually measuring.
/// </summary>
[AttributeUsage(AttributeTargets.Assembly, AllowMultiple = false)]
public sealed class TestActivityRegistrar : BeforeAfterTestAttribute
{
    public override void Before(MethodInfo methodUnderTest) => TestActivity.Enter(byRegistrar: true);

    public override void After(MethodInfo methodUnderTest) => TestActivity.Exit();
}
