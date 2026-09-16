using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Listening;
using PagentOS.Companion.Audio.Listening.Spotting;
using PagentOS.Companion.Audio.Tests.Support;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

/// <summary>B47 rows 241/253: the offline template engine, on synthetic phrases only.</summary>
public sealed class SpottingTests
{
    private static readonly string Wake = DeviceVoiceContract.WakeWordPhraseId;
    private static readonly string Snooze = DeviceVoiceContract.CommandAlarmSnooze;
    private static readonly string Off = DeviceVoiceContract.CommandListeningOff;

    private static TemplateKeywordSpotter Spotter()
        => new(Phrases.Enrolled((Wake, Phrases.Wake), (Snooze, Phrases.Snooze), (Off, Phrases.ListeningOff)));

    [Fact]
    public void A_phrase_said_at_another_speed_and_pitch_is_recognised_and_a_different_phrase_is_not()
    {
        var spotter = Spotter();
        var commands = new[] { Snooze, Off };

        var snooze = spotter.Classify(Phrases.Features(Phrases.Say(Phrases.Snooze, stretch: 1.05, pitch: 0.99, seed: 40)), commands);
        var off = spotter.Classify(Phrases.Features(Phrases.Say(Phrases.ListeningOff, stretch: 0.95, pitch: 1.01, seed: 41)), commands);
        var neither = spotter.Classify(Phrases.Features(Phrases.Say(Phrases.Request, seed: 42)), commands);
        var stop = spotter.Classify(Phrases.Features(Phrases.Say(Phrases.Stop, seed: 43)), commands);

        Assert.Equal(Snooze, snooze?.PhraseId);
        Assert.Equal(Off, off?.PhraseId);
        Assert.Null(neither);
        Assert.Null(stop);
        Assert.True(snooze!.Distance <= snooze.Threshold);
    }

    [Fact]
    public void Only_the_allowed_phrases_are_considered()
    {
        var spotter = Spotter();
        var features = Phrases.Features(Phrases.Say(Phrases.Snooze, seed: 44));

        Assert.Null(spotter.Classify(features, [Off]));
        Assert.Null(spotter.Classify(features, []));
    }

    [Fact]
    public void The_wake_word_is_found_at_the_start_of_a_breath_and_its_end_is_reported()
    {
        var spotter = Spotter();
        var breath = Phrases.Say([.. Phrases.Wake, .. Phrases.Request], seed: 45);

        var hit = spotter.SpotPrefix(Phrases.Features(breath), Wake);

        Assert.NotNull(hit);
        // The wake phrase is 480 ms long, which is ~46 feature frames at a 10 ms hop.
        Assert.InRange(hit!.EndFrame, 30, 70);
        Assert.Null(spotter.SpotPrefix(Phrases.Features(Phrases.Say([.. Phrases.Request, .. Phrases.Wake], seed: 46)), Wake));
        Assert.Null(spotter.SpotPrefix(Phrases.Features(Phrases.Say(Phrases.Snooze, seed: 47)), Wake));
    }

    [Fact]
    public void Thresholds_are_measured_from_the_templates_and_a_single_take_is_not_enough()
    {
        var set = Phrases.Enrolled((Snooze, Phrases.Snooze));
        var spotter = new TemplateKeywordSpotter(set);
        Assert.True(spotter.IsAvailable(Snooze));
        Assert.True(spotter.ThresholdFor(Snooze) > 0);

        var single = new KeywordTemplateSet(Phrases.Format.SampleRate);
        single.Add(Off, set.For(Snooze)[0]);
        var thin = new TemplateKeywordSpotter(single);
        Assert.False(thin.IsAvailable(Off));
        Assert.Contains(Off, thin.UnavailableReason);
    }

    [Fact]
    public void Nothing_but_the_contracts_phrases_can_be_enrolled()
    {
        var set = new KeywordTemplateSet(Phrases.Format.SampleRate);
        Assert.Throws<ArgumentException>(() => set.Add("delete.files", new KeywordTemplate([new float[MfccExtractor.Coefficients]])));
        Assert.True(KeywordTemplateSet.IsValidPhraseId(Wake));
        foreach (var rule in DeviceVoiceContract.OfflineCommands)
        {
            Assert.True(KeywordTemplateSet.IsValidPhraseId(rule.Id));
        }
    }

    [Fact]
    public void Templates_keep_at_most_five_takes_per_phrase()
    {
        var set = Phrases.Enrolled((Snooze, Phrases.Snooze));
        var template = set.For(Snooze)[0];
        for (var i = 0; i < 10; i++)
        {
            set.Add(Snooze, template);
        }

        Assert.Equal(KeywordTemplateSet.MaxTemplatesPerPhrase, set.For(Snooze).Count);
    }

    [Fact]
    public void The_template_file_is_what_the_protector_wrote_and_round_trips()
    {
        var directory = Path.Combine(Path.GetTempPath(), "pagentos-b47-" + Guid.NewGuid().ToString("N"));
        try
        {
            var path = FileKeywordTemplateStore.DefaultPath(directory);
            static byte[] Flip(byte[] data) => data.Select(b => (byte)(b ^ 0x5A)).ToArray();
            var store = new FileKeywordTemplateStore(path, Flip, Flip);
            var set = Phrases.Enrolled((Wake, Phrases.Wake), (Snooze, Phrases.Snooze));

            store.Save(set);
            var raw = File.ReadAllBytes(path);
            var loaded = store.Load();

            // The bytes on disk are the protector's output, never the plain JSON.
            Assert.DoesNotContain("template_dtw", System.Text.Encoding.UTF8.GetString(raw), StringComparison.Ordinal);
            Assert.NotNull(loaded);
            Assert.Equal(set.SampleRate, loaded!.SampleRate);
            Assert.Equal(set.For(Wake).Count, loaded.For(Wake).Count);
            Assert.True(new TemplateKeywordSpotter(loaded).IsAvailable(Snooze));
            Assert.False(File.Exists(path + ".tmp"));

            // A file the protector cannot open is "nothing enrolled", never a crash.
            var broken = new FileKeywordTemplateStore(path, Flip, _ => throw new InvalidDataException("not ours"));
            Assert.Null(broken.Load());
        }
        finally
        {
            if (Directory.Exists(directory))
            {
                Directory.Delete(directory, recursive: true);
            }
        }
    }

    [Fact]
    public void Enrollment_trims_silence_refuses_what_is_not_a_phrase_and_zeroes_the_recording()
    {
        var recording = new List<AudioFrame>([.. Phrases.Quiet(400), .. Phrases.Say(Phrases.Snooze, seed: 50), .. Phrases.Quiet(400)]);
        var result = KeywordEnrollment.FromRecording(recording, Phrases.Format);

        Assert.True(result.Accepted);
        Assert.InRange(result.SpokenMs, 420, 560);
        Assert.All(recording, frame => Assert.All(frame.Pcm16, b => Assert.Equal(0, b)));

        Assert.Equal("too_quiet", KeywordEnrollment.FromRecording(Phrases.Quiet(1000), Phrases.Format).Detail);
        Assert.Equal("too_short", KeywordEnrollment.FromRecording([.. Phrases.Quiet(200), .. Phrases.Say([(500, 80)]), .. Phrases.Quiet(200)], Phrases.Format).Detail);
        var paragraph = Enumerable.Repeat(Phrases.Request, 4).SelectMany(p => p).ToArray();
        Assert.Equal("too_long", KeywordEnrollment.FromRecording(Phrases.Say(paragraph), Phrases.Format).Detail);
    }

    [Fact]
    public async Task The_enrollment_recorder_takes_a_bounded_window_and_stops_the_capture()
    {
        var capture = new Fakes.FakeCapture("cap", Phrases.Format);
        var recording = EnrollmentRecorder.RecordAsync(capture, TimeSpan.FromMilliseconds(200), CancellationToken.None);
        Assert.True(await TestSupport.WaitForAsync(() => capture.Started));
        foreach (var frame in Phrases.Quiet(400))
        {
            capture.Feed(frame);
        }

        var frames = await recording;
        Assert.Equal(10, frames.Count);
        Assert.False(capture.Started);
    }

    [Fact]
    public void Mfcc_frames_have_the_documented_shape_and_hop()
    {
        var frames = MfccExtractor.Extract(new short[Phrases.Format.SampleRate], Phrases.Format.SampleRate);

        // One second at a 10 ms hop with a 25 ms window.
        Assert.Equal(98, frames.Length);
        Assert.All(frames, f => Assert.Equal(MfccExtractor.Coefficients, f.Length));
        Assert.All(frames, f => Assert.All(f, v => Assert.True(float.IsFinite(v))));
    }

    [Fact]
    public void Dtw_distance_is_zero_for_identical_sequences_and_infinite_for_wildly_different_lengths()
    {
        var a = Phrases.Features(Phrases.Say(Phrases.Snooze, seed: 60));
        Assert.Equal(0, Dtw.Distance(a, a), 6);
        Assert.True(double.IsPositiveInfinity(Dtw.Distance(a, a.Take(a.Length / 3).ToArray())));
        Assert.True(double.IsPositiveInfinity(Dtw.Distance(a, [])));
    }
}
