using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

/// <summary>
/// The Device Service's <see cref="IPointerStreamFrameSink"/> (ADR-0199): a <c>pointer_stream</c>
/// batch from the broker goes to the companion over the pipe as a one-way <c>pointer.stream</c>
/// request and nowhere else. Session 0 has no pointer to move; the service neither applies a
/// frame nor keeps one.
/// </summary>
public sealed class PipePointerStreamForwarder(CompanionPipeServer pipeServer) : IPointerStreamFrameSink
{
    public async ValueTask<bool> ForwardAsync(PointerStreamMessage frame, CancellationToken cancellationToken)
        => await pipeServer.ForwardPointerStreamAsync(frame, cancellationToken).ConfigureAwait(false);
}
