using PagentOS.Companion.Audio.Audio;
using PagentOS.Companion.Audio.Fakes;
using Xunit;

namespace PagentOS.Companion.Audio.Tests;

public sealed class DeviceSelectorTests
{
    private static readonly AudioDeviceInfo Laptop = FakeDeviceCatalog.LaptopMic(isDefault: true, isCommunications: true);
    private static readonly AudioDeviceInfo Headset = FakeDeviceCatalog.HeadsetMic();

    [Fact]
    public void The_owners_explicit_choice_wins_when_present()
    {
        var selector = new AudioDeviceSelector(AudioDirection.Capture, preferredDeviceId: Headset.Id);
        var choice = selector.Choose(new[] { Laptop, Headset });
        Assert.Equal(Headset.Id, choice!.DeviceId);
        Assert.Equal("owner_preference", choice.Reason);
    }

    [Fact]
    public void Without_a_preference_the_default_communications_device_wins()
    {
        var selector = new AudioDeviceSelector(AudioDirection.Capture);
        var headsetAsComms = Headset with { IsDefaultCommunications = true };
        var choice = selector.Choose(new[] { Laptop with { IsDefaultCommunications = false }, headsetAsComms });
        Assert.Equal(Headset.Id, choice!.DeviceId);
        Assert.Equal("default_communications", choice.Reason);
    }

    [Fact]
    public void Then_a_headset_then_the_default_then_the_first()
    {
        var selector = new AudioDeviceSelector(AudioDirection.Capture);
        var plainLaptop = Laptop with { IsDefault = false, IsDefaultCommunications = false };

        Assert.Equal("headset", selector.Choose(new[] { plainLaptop, Headset })!.Reason);
        Assert.Equal("default", selector.Choose(new[] { plainLaptop, Laptop with { IsDefaultCommunications = false } })!.Reason);
        Assert.Equal("first_available", selector.Choose(new[] { plainLaptop })!.Reason);
        Assert.Null(selector.Choose(new[] { FakeDeviceCatalog.LaptopSpeakers() }));
    }

    [Fact]
    public void An_unplugged_active_device_is_an_immediate_switch()
    {
        var selector = new AudioDeviceSelector(AudioDirection.Capture);
        selector.MarkActive(Headset.Id);

        var change = selector.Reconcile(new[] { Laptop });

        Assert.NotNull(change);
        Assert.True(change!.Immediate);
        Assert.Equal(Headset.Id, change.FromDeviceId);
        Assert.Equal(Laptop.Id, change.ToDeviceId);
        Assert.Equal("active_device_removed", change.Reason);
    }

    [Fact]
    public void A_headset_plugged_in_is_a_deferred_switch_and_nothing_changes_when_nothing_changed()
    {
        var selector = new AudioDeviceSelector(AudioDirection.Capture);
        selector.MarkActive(Laptop.Id);
        Assert.Null(selector.Reconcile(new[] { Laptop }));

        var change = selector.Reconcile(new[] { Laptop with { IsDefaultCommunications = false }, Headset with { IsDefaultCommunications = true } });

        Assert.NotNull(change);
        Assert.False(change!.Immediate);
        Assert.Equal("default_communications", change.Reason);
    }

    [Fact]
    public void The_preferred_device_reappearing_pulls_the_selection_back_deferred()
    {
        var selector = new AudioDeviceSelector(AudioDirection.Capture, Headset.Id);
        selector.MarkActive(Laptop.Id);

        var change = selector.Reconcile(new[] { Laptop, Headset });

        Assert.Equal(Headset.Id, change!.ToDeviceId);
        Assert.Equal("owner_preference", change.Reason);
        Assert.False(change.Immediate);
    }

    [Fact]
    public void Direction_is_respected()
    {
        var selector = new AudioDeviceSelector(AudioDirection.Render);
        var choice = selector.Choose(new[] { Laptop, FakeDeviceCatalog.LaptopSpeakers(), FakeDeviceCatalog.HeadsetEarpiece() });
        Assert.Equal("ren-laptop", choice!.DeviceId);
    }

    [Theory]
    [InlineData("Kulaklık Mikrofonu (USB Audio)", AudioFormFactor.Headset)]
    [InlineData("Headset Microphone (Jabra)", AudioFormFactor.Headset)]
    [InlineData("Hoparlör (Realtek(R) Audio)", AudioFormFactor.Speakers)]
    [InlineData("Mikrofon Dizisi (Intel Smart Sound)", AudioFormFactor.BuiltIn)]
    [InlineData("Microphone Array (Realtek)", AudioFormFactor.BuiltIn)]
    [InlineData("Hands-Free AG Audio (Galaxy Buds)", AudioFormFactor.Handset)]
    [InlineData("WH-1000XM4 Headphones", AudioFormFactor.Headphones)]
    [InlineData("Something Odd", AudioFormFactor.Unknown)]
    [InlineData("", AudioFormFactor.Unknown)]
    public void Form_factor_is_guessed_from_the_name_in_Turkish_and_English(string name, AudioFormFactor expected)
    {
        Assert.Equal(expected, AudioFormFactorHeuristics.FromName(name));
    }

    [Fact]
    public void Headset_like_means_the_microphone_cannot_hear_the_speakers()
    {
        Assert.True(FakeDeviceCatalog.HeadsetEarpiece().IsHeadsetLike);
        Assert.False(FakeDeviceCatalog.LaptopSpeakers().IsHeadsetLike);
    }
}
