namespace PagentOS.Companion.Audio.Media;

/// <summary>
/// Provider-neutral events arriving over the media leg. <c>ReceivedAt</c> is the client's
/// monotonic timestamp at decode time, which is what the latency metrics need.
/// </summary>
public abstract record ProviderEvent(long ReceivedAt);

public sealed record SessionReadyEvent(long ReceivedAt, string ProviderSessionId) : ProviderEvent(ReceivedAt);

public sealed record InputSpeechStartedEvent(long ReceivedAt) : ProviderEvent(ReceivedAt);

public sealed record InputSpeechStoppedEvent(long ReceivedAt) : ProviderEvent(ReceivedAt);

public sealed record ResponseStartedEvent(long ReceivedAt, string ResponseId) : ProviderEvent(ReceivedAt);

public sealed record AudioDeltaEvent(long ReceivedAt, string ResponseId, byte[] Pcm16) : ProviderEvent(ReceivedAt);

/// <summary><paramref name="Status"/>: completed | cancelled | failed | incomplete.</summary>
public sealed record ResponseDoneEvent(long ReceivedAt, string ResponseId, string Status) : ProviderEvent(ReceivedAt);

public sealed record ToolCallEvent(long ReceivedAt, string CallId, string Name, string ArgumentsJson) : ProviderEvent(ReceivedAt);

/// <summary>Transcription of the owner's input; <paramref name="Final"/> when the provider closed the item.</summary>
public sealed record TranscriptDeltaEvent(long ReceivedAt, string Text, bool Final) : ProviderEvent(ReceivedAt);

public sealed record ProviderErrorEvent(long ReceivedAt, string Code, string Message) : ProviderEvent(ReceivedAt);

public sealed record DisconnectedEvent(long ReceivedAt, string Reason) : ProviderEvent(ReceivedAt);

/// <summary>Client → provider intents; the wire codec turns each into provider JSON.</summary>
public abstract record ProviderCommand;

public sealed record AppendAudioCommand(byte[] Pcm16) : ProviderCommand;

public sealed record CommitTurnCommand : ProviderCommand;

public sealed record CancelResponseCommand : ProviderCommand;

/// <summary>
/// <paramref name="Final"/> false = the provisional output of a long-running tool (status
/// running + preamble) that the provider should speak now; true = the real result, which
/// for the first submission is the function output and for a later completion is a
/// follow-up item, since a function output cannot be amended once sent.
/// </summary>
public sealed record ToolResultCommand(string CallId, string OutputJson, bool Final, bool FollowUp) : ProviderCommand;

public sealed record SayCommand(string Text) : ProviderCommand;

public sealed record SessionConfigureCommand(
    string? Instructions,
    System.Text.Json.Nodes.JsonArray? Tools,
    Turn.EndOfTurnMode EndOfTurn,
    System.Text.Json.Nodes.JsonObject? ProviderSessionConfig) : ProviderCommand;
