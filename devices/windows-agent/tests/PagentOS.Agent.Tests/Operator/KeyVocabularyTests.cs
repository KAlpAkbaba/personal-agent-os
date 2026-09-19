using PagentOS.Agent.Core.Commands;
using PagentOS.SessionCompanion.Operator;
using Xunit;

namespace PagentOS.Agent.Tests.Operator;

/// <summary>
/// <c>keyboard.key</c> takes a named key or ONE ASCII letter or digit (owner, 2026-09-19:
/// "0 tuşuna bas gibi komutlar gitmiyor"). A page's shortcuts are single characters, and they
/// must arrive as a virtual key - a real keydown - not as typed text. Everything else is still
/// text and still refused before any event exists.
/// </summary>
public sealed class KeyVocabularyTests
{
    [Theory]
    [InlineData("0", 0x30)]
    [InlineData("9", 0x39)]
    [InlineData("k", 0x4B)]
    [InlineData("K", 0x4B)]
    public void A_single_letter_or_digit_is_a_key_and_maps_to_its_own_virtual_key(string key, int vk)
    {
        KeyMap.ValidateKey(key);
        Assert.True(KeyMap.TryKey(key, allowCharacters: true, out var actual, out var extended));
        Assert.Equal((ushort)vk, actual);
        Assert.False(extended);
    }

    [Theory]
    [InlineData("enter")]
    [InlineData("home")]
    [InlineData("f5")]
    public void Named_keys_are_keys_as_before(string key) => KeyMap.ValidateKey(key);

    [Theory]
    [InlineData("ab")]
    [InlineData("ç")]
    [InlineData("!")]
    [InlineData(" ")]
    [InlineData("")]
    [InlineData("merhaba")]
    public void Text_is_still_refused_as_a_key(string key)
    {
        var refused = Assert.Throws<CapabilityException>(() => KeyMap.ValidateKey(key));
        Assert.Contains("keyboard.type", refused.Message, StringComparison.Ordinal);
    }
}
