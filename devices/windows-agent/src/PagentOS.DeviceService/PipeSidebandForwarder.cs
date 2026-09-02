using PagentOS.Agent.Core.Protocol;

namespace PagentOS.DeviceService;

/// <summary>
/// The Device Service's <see cref="ISidebandFrameSink"/>: a <c>voice_sideband</c> frame from
/// the broker goes to the companion over the pipe and nowhere else (M12, ADR-0039). The
/// service does not look inside it — audio, transcripts and the owner's conversation are
/// the owner session's business, not Session 0's.
/// </summary>
public sealed class PipeSidebandForwarder(CompanionPipeServer pipeServer) : ISidebandFrameSink
{
    public async ValueTask<bool> ForwardAsync(VoiceSidebandMessage frame, CancellationToken cancellationToken)
        => await pipeServer.ForwardSidebandAsync(frame.ToFrame(), cancellationToken).ConfigureAwait(false);
}
