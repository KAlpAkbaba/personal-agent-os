using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Audio.Processing;
using PagentOS.Companion.Audio.Audio.Vad;
using PagentOS.Companion.Audio.Tests.Support;
using PagentOS.Companion.Audio.Timing;
using PagentOS.Companion.Audio.Turn;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class HesitationGuardTests
{
    private static HesitationVerdict Evaluate(string? tail, double runMs = 0, double std = 10)
        => new HesitationGuard().Evaluate(new HesitationContext(tail, runMs, std));

    [Theory]
    [InlineData("yarın toplantı var")]
    [InlineData("ertele")]
    [InlineData("")]
    [InlineData(null)]
    public void A_plain_ending_needs_only_the_base_silence(string? tail)
    {
        var verdict = Evaluate(tail);
        Assert.Equal(500, verdict.RequiredTrailingSilenceMs);
        Assert.Equal(0, verdict.ExtensionMs);
        Assert.Equal("none", verdict.Reason);
    }

    [Theory]
    [InlineData("yarın toplantıyı şey", "şey")]
    [InlineData("ŞEY", "şey")]
    [InlineData("bunu yani", "yani")]
    [InlineData("o hani", "hani")]
    [InlineData("ııı", "ııı")]
    [InlineData("eee", "eee")]
    [InlineData("şeyyy", "şeyyy")]
    [InlineData("toplantıyı şey...", "şey")]
    [InlineData("hmm", "hmm")]
    public void Fillers_buy_the_filler_extension(string tail, string token)
    {
        var verdict = Evaluate(tail);
        Assert.Equal(1400, verdict.RequiredTrailingSilenceMs);
        Assert.Equal(900, verdict.ExtensionMs);
        Assert.Equal("filler:" + token, verdict.Reason);
    }

    [Theory]
    [InlineData("toplantıyı ertele ve", "ve")]
    [InlineData("gelirim ama", "ama")]
    [InlineData("olmaz çünkü", "çünkü")]
    [InlineData("bugün ya da", "da")]
    public void Connectives_buy_the_connective_extension(string tail, string token)
    {
        var verdict = Evaluate(tail);
        Assert.Equal(1100, verdict.RequiredTrailingSilenceMs);
        Assert.Equal("connective:" + token, verdict.Reason);
    }

    [Fact]
    public void A_steady_drawn_out_final_sound_counts_as_prolongation()
    {
        var verdict = Evaluate("toplantıyı", runMs: 400, std: 1.0);
        Assert.Equal(1000, verdict.RequiredTrailingSilenceMs);
        Assert.Equal("prolongation", verdict.Reason);
    }

    [Fact]
    public void A_short_or_uneven_final_sound_is_not_prolongation()
    {
        Assert.Equal("none", Evaluate("toplantıyı", runMs: 300, std: 1.0).Reason);
        Assert.Equal("none", Evaluate("toplantıyı", runMs: 600, std: 6.0).Reason);
    }

    [Fact]
    public void Evidence_adds_up_and_is_capped()
    {
        var combined = Evaluate("yani şey", runMs: 500, std: 0.5);
        Assert.Equal(1900, combined.RequiredTrailingSilenceMs);
        Assert.Equal("filler:şey+prolongation", combined.Reason);

        var guard = new HesitationGuard(new HesitationGuardOptions { FillerExtensionMs = 5000 });
        var capped = guard.Evaluate(new HesitationContext("şey", 0, 10));
        Assert.Equal(2500, capped.RequiredTrailingSilenceMs);
    }

    [Fact]
    public void Turkish_folding_handles_dotted_and_dotless_I()
    {
        Assert.Equal("iıi ışık", TurkishText.Fold("İIi IŞIK"));
        Assert.Equal(new[] { "şey", "ertele" }, TurkishText.Tokens("Şey... ertele!"));
        Assert.True(TurkishText.IsElongated("şeeey"));
        Assert.False(TurkishText.IsElongated("şey"));
    }

    // ---- the detector: frame-driven, guard in the loop ----

    private static (ClientEndOfTurnDetector Detector, ManualTimeProvider Time, SyntheticAudio Synth) NewDetector()
    {
        var time = new ManualTimeProvider();
        var detector = new ClientEndOfTurnDetector(new EnergyVad(), new HesitationGuard(), time);
        return (detector, time, new SyntheticAudio(TestSupport.Format));
    }

    private static List<TurnEvent> Feed(ClientEndOfTurnDetector detector, ManualTimeProvider time, IEnumerable<AudioFrame> frames, AudioProcessingContext? context = null)
    {
        var events = new List<TurnEvent>();
        foreach (var frame in frames)
        {
            var ev = detector.Process(frame, context ?? new AudioProcessingContext(false, true));
            time.AdvanceMs(frame.DurationMs);
            if (ev is not null)
            {
                events.Add(ev);
            }
        }

        return events;
    }

    private static IEnumerable<AudioFrame> Silence(int ms)
    {
        for (var fed = 0; fed < ms; fed += 20)
        {
            yield return TestSupport.Quiet();
        }
    }

    [Fact]
    public void Speech_starts_after_the_onset_debounce_and_ends_after_the_base_silence()
    {
        var (detector, time, synth) = NewDetector();

        var started = Feed(detector, time, TestSupport.ModulatedSpeech(synth, 300, time.GetTimestamp));
        var ended = Feed(detector, time, Silence(600));

        var start = Assert.Single(started);
        Assert.Equal(TurnEventKind.SpeechStarted, start.Kind);
        var end = Assert.Single(ended);
        Assert.Equal(TurnEventKind.SpeechEnded, end.Kind);
        Assert.Equal(500, end.RequiredSilenceMs);
        Assert.Equal(500, end.TrailingSilenceMs, precision: 3);
        Assert.Equal("none", end.Reason);
        Assert.False(detector.InSpeech);
    }

    [Fact]
    public void A_pause_after_sey_does_not_end_the_turn_until_the_extended_silence_passes()
    {
        var (detector, time, synth) = NewDetector();
        Feed(detector, time, TestSupport.ModulatedSpeech(synth, 300, time.GetTimestamp));
        detector.ObserveTranscript("yarın toplantıyı şey");

        var afterSevenHundred = Feed(detector, time, Silence(700));
        Assert.Empty(afterSevenHundred);
        Assert.True(detector.InSpeech);

        var later = Feed(detector, time, Silence(800));
        var end = Assert.Single(later);
        Assert.Equal(1400, end.RequiredSilenceMs);
        Assert.Equal(900, end.HesitationExtensionMs);
        Assert.Equal("filler:şey", end.Reason);
    }

    [Fact]
    public void Resuming_speech_after_a_filler_pause_keeps_one_turn_open()
    {
        var (detector, time, synth) = NewDetector();
        Feed(detector, time, TestSupport.ModulatedSpeech(synth, 300, time.GetTimestamp));
        detector.ObserveTranscript("şey");
        Feed(detector, time, Silence(600));

        var resumed = Feed(detector, time, TestSupport.ModulatedSpeech(synth, 200, time.GetTimestamp));
        Assert.Empty(resumed);
        // The transcript of what was just said arrives after its audio, as a provider delivers it.
        detector.ObserveTranscript("ertele");

        var ended = Feed(detector, time, Silence(600));
        var end = Assert.Single(ended);
        Assert.Equal("none", end.Reason);
        Assert.Equal(500, end.RequiredSilenceMs);
    }

    [Fact]
    public void A_steady_drawn_vowel_extends_the_silence_acoustically_without_any_transcript()
    {
        var (detector, time, synth) = NewDetector();
        var steady = Enumerable.Range(0, 20).Select(_ => synth.Tone(20, time.GetTimestamp(), amplitude: 0.3));
        Feed(detector, time, steady);

        var afterBase = Feed(detector, time, Silence(600));
        Assert.Empty(afterBase);

        var end = Assert.Single(Feed(detector, time, Silence(500)));
        Assert.Equal(1000, end.RequiredSilenceMs);
        Assert.Equal("prolongation", end.Reason);
    }

    [Fact]
    public void While_the_assistant_is_audible_through_loudspeakers_the_onset_needs_a_margin_over_the_echo()
    {
        var (detector, time, synth) = NewDetector();
        var quietVoice = Enumerable.Range(0, 5).Select(_ => synth.Tone(20, time.GetTimestamp(), amplitude: 0.03)); // about -33 dBFS
        var loudspeaker = new AudioProcessingContext(PlaybackActive: true, RenderIsLoudspeaker: true);

        Assert.Empty(Feed(detector, time, quietVoice, loudspeaker));

        var headset = new AudioProcessingContext(PlaybackActive: true, RenderIsLoudspeaker: false);
        var again = Enumerable.Range(0, 5).Select(_ => synth.Tone(20, time.GetTimestamp(), amplitude: 0.03));
        Assert.Single(Feed(detector, time, again, headset));
    }
}
