using System.Text.RegularExpressions;

namespace PagentOS.Agent.Core.Protocol;

/// <summary>
/// Semantic validation on top of deserialization, mirroring the constraints of
/// packages/schemas/device-protocol.schema.json for frames the agent receives.
/// </summary>
public static partial class MessageValidator
{
    [GeneratedRegex("^[a-z][a-z0-9_.]{1,63}$")]
    private static partial Regex CapabilityRegex();

    public static void ValidateInbound(ProtocolMessage message)
    {
        switch (message)
        {
            case ChallengeMessage challenge:
                ValidateChallenge(challenge);
                break;
            case WelcomeMessage welcome:
                ValidateWelcome(welcome);
                break;
            case HeartbeatMessage heartbeat:
                RequireNonNegative(heartbeat.Seq, "heartbeat.seq");
                break;
            case HeartbeatAckMessage heartbeatAck:
                RequireNonNegative(heartbeatAck.Seq, "heartbeat_ack.seq");
                break;
            case CommandMessage command:
                ValidateCommandEnvelope(command.Command);
                break;
            case CancelMessage cancel:
                RequireUuid(cancel.CommandId, "cancel.command_id");
                break;
            case ErrorMessage error:
                ValidateErrorObject(error.Error);
                break;
            default:
                // hello/auth/command_ack are agent->broker; nothing extra to validate inbound.
                break;
        }
    }

    public static void ValidateCommandEnvelope(CommandEnvelope command)
    {
        RequireUuid(command.CommandId, "command.command_id");
        if (command.IdempotencyKey.Length is < 8 or > 128)
        {
            throw new ProtocolValidationException("command.idempotency_key length must be 8..128");
        }

        if (!CapabilityRegex().IsMatch(command.Capability))
        {
            throw new ProtocolValidationException("command.capability does not match required pattern");
        }

        if (command.TraceId.Length is < 1 or > 128)
        {
            throw new ProtocolValidationException("command.trace_id length must be 1..128");
        }
    }

    public static void ValidateErrorObject(ErrorObject error)
    {
        if (!ErrorClasses.All.Contains(error.Class))
        {
            throw new ProtocolValidationException($"unknown error class '{error.Class}'");
        }

        if (error.Message.Length > ProtocolConstants.MaxErrorMessageLength)
        {
            throw new ProtocolValidationException("error.message exceeds 2000 characters");
        }
    }

    private static void ValidateChallenge(ChallengeMessage challenge)
    {
        if (challenge.Nonce.Length is < 16 or > 128)
        {
            throw new ProtocolValidationException("challenge.nonce length must be 16..128");
        }

        try
        {
            _ = Convert.FromBase64String(challenge.Nonce);
        }
        catch (FormatException)
        {
            throw new ProtocolValidationException("challenge.nonce is not valid base64");
        }
    }

    private static void ValidateWelcome(WelcomeMessage welcome)
    {
        RequireUuid(welcome.SessionId, "welcome.session_id");
        if (welcome.HeartbeatIntervalS <= 0 || welcome.HeartbeatIntervalS > 300)
        {
            throw new ProtocolValidationException("welcome.heartbeat_interval_s must be in (0, 300]");
        }
    }

    private static void RequireUuid(string value, string field)
    {
        if (!Guid.TryParse(value, out _))
        {
            throw new ProtocolValidationException($"{field} is not a valid uuid");
        }
    }

    private static void RequireNonNegative(long value, string field)
    {
        if (value < 0)
        {
            throw new ProtocolValidationException($"{field} must be >= 0");
        }
    }
}
