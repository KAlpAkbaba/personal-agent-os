using System.Runtime.InteropServices;
using System.Windows.Automation;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// A window that closes while the operator is describing it is an observation, not a crash.
///
/// CI run 34230759954 (2026-09-08, on a documentation-only commit) failed the modal-detection
/// fact with <c>COMException: Catastrophic failure (0x8000FFFF E_UNEXPECTED)</c> raised out of
/// <see cref="UiAutomationInspector.Describe"/> while a save dialog was being dismissed. UI
/// Automation reports a vanished element in TWO shapes — the typed
/// <see cref="ElementNotAvailableException"/>, which this code handled everywhere and which
/// <c>OperatorCapabilities.UiInvoke</c> explicitly expects after a dialog button closes its own
/// dialog, and a raw COM error carrying one of a few HRESULTs, which nothing caught. On the
/// owner's machine the second shape is ordinary: windows close while they are being read.
///
/// These facts pin the one decision both shapes now go through, INCLUDING what must NOT be
/// swallowed: an unrelated COM failure still surfaces, because hiding it would hide a defect.
/// </summary>
public sealed class VanishedElementTests
{
    [Fact]
    public void The_typed_element_not_available_exception_means_the_element_is_gone()
    {
        Assert.True(UiAutomationInspector.IsElementGone(new ElementNotAvailableException()));
    }

    [Theory]
    [InlineData(unchecked((int)0x80040201))] // UIA_E_ELEMENTNOTAVAILABLE
    [InlineData(unchecked((int)0x8000FFFF))] // E_UNEXPECTED — the shape the runner met
    [InlineData(unchecked((int)0x80010108))] // RPC_E_DISCONNECTED
    [InlineData(unchecked((int)0x800706BA))] // RPC_S_SERVER_UNAVAILABLE
    public void A_com_failure_that_means_the_provider_went_away_is_gone(int hresult)
    {
        var exception = new COMException("provider went away", hresult);
        Assert.True(UiAutomationInspector.IsElementGone(exception));
    }

    [Theory]
    [InlineData(unchecked((int)0x80070005))] // E_ACCESSDENIED
    [InlineData(unchecked((int)0x80004001))] // E_NOTIMPL
    [InlineData(unchecked((int)0x8007000E))] // E_OUTOFMEMORY
    public void An_unrelated_com_failure_is_not_swallowed(int hresult)
    {
        // The narrowness is the point: a permission failure or an exhausted machine must reach
        // the caller as itself, never be reported as "the window closed".
        var exception = new COMException("something else entirely", hresult);
        Assert.False(UiAutomationInspector.IsElementGone(exception));
    }

    [Fact]
    public void An_ordinary_exception_is_not_a_vanished_element()
    {
        Assert.False(UiAutomationInspector.IsElementGone(new InvalidOperationException()));
        Assert.False(UiAutomationInspector.IsElementGone(new TimeoutException()));
    }
}
