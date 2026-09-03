using System.Text;
using PagentOS.Agent.Core.Protocol;
using PagentOS.SessionCompanion;
using Xunit;

namespace PagentOS.Agent.Tests;

/// <summary>
/// The two companion-side guards on what a worker may say, in isolation: the bounded line
/// reader that keeps one stdout line from growing without limit, and the sanitiser that
/// decides what of the worker's stderr is repeated in the companion log. The process-level
/// behaviour (kill, restart, log content) is proven in <see cref="BrowserWorkerHostTests"/>.
/// </summary>
public sealed class BrowserWorkerGuardTests
{
    private static BoundedLineReader Reader(string content, int max)
        => new(new MemoryStream(Encoding.UTF8.GetBytes(content)), max);

    // ------------------------------------------------------------------ bounded reader

    [Fact]
    public async Task Lines_are_split_on_lf_with_an_optional_cr_and_the_last_unterminated_line_is_returned()
    {
        var reader = Reader("first\r\nsecond\n\nlast", max: 100);

        Assert.Equal("first", await reader.ReadLineAsync(CancellationToken.None));
        Assert.Equal("second", await reader.ReadLineAsync(CancellationToken.None));
        Assert.Equal("", await reader.ReadLineAsync(CancellationToken.None));
        Assert.Equal("last", await reader.ReadLineAsync(CancellationToken.None));
        Assert.Null(await reader.ReadLineAsync(CancellationToken.None));
        Assert.Null(await reader.ReadLineAsync(CancellationToken.None));
    }

    [Fact]
    public async Task A_line_of_exactly_the_ceiling_is_returned_and_one_byte_more_throws_without_reading_it_all()
    {
        var exact = new string('a', 64);
        Assert.Equal(exact, await Reader(exact + "\n", max: 64).ReadLineAsync(CancellationToken.None));
        Assert.Equal(exact, await Reader(exact, max: 64).ReadLineAsync(CancellationToken.None));

        var over = Reader(new string('b', 65) + "\nnext\n", max: 64);
        var ex = await Assert.ThrowsAsync<LineTooLongException>(() => over.ReadLineAsync(CancellationToken.None));
        Assert.Equal(64, ex.MaxLineBytes);
        Assert.True(ex.SeenBytes >= 65);

        // A very long line spanning many chunks is refused as soon as the ceiling is
        // crossed, never assembled: the reader has consumed at most one chunk beyond it.
        var counting = new CountingStream(Encoding.UTF8.GetBytes(new string('c', 1024 * 1024)));
        var bounded = new BoundedLineReader(counting, maxLineBytes: 1000);
        await Assert.ThrowsAsync<LineTooLongException>(() => bounded.ReadLineAsync(CancellationToken.None));
        Assert.True(counting.BytesRead <= 32 * 1024, $"read {counting.BytesRead} bytes for a 1000-byte ceiling");
    }

    [Fact]
    public async Task Multibyte_utf8_survives_chunk_boundaries()
    {
        var text = string.Concat(Enumerable.Repeat("Gönder—şifre ✓ ", 3000));
        var reader = new BoundedLineReader(new MemoryStream(Encoding.UTF8.GetBytes(text + "\n")), maxLineBytes: 1024 * 1024);

        Assert.Equal(text, await reader.ReadLineAsync(CancellationToken.None));
    }

    [Fact]
    public void The_hosts_ceiling_is_four_times_the_result_cap()
    {
        Assert.Equal(4 * BrowserCapabilities.MaxResultBytes, BrowserWorkerHost.MaxStdoutLineBytes);
        Assert.Throws<ArgumentOutOfRangeException>(() => new BoundedLineReader(Stream.Null, 0));
    }

    // ------------------------------------------------------------------ sanitiser

    [Theory]
    [InlineData("plain text", "plain text")]
    [InlineData("", "")]
    [InlineData("fake-worker: cancel request_id=abc", "fake-worker: cancel request_id=abc")]
    [InlineData("navigate https://example.org/a/b?x=1&token=t", "navigate https://example.org/a/b")]
    [InlineData("see https://example.org/page#section end", "see https://example.org/page end")]
    [InlineData("two https://a.example/?q=1 and http://b.example/p?r=2 done", "two https://a.example/ and http://b.example/p done")]
    [InlineData("file:///C:/data/x.html?v=3", "file:///C:/data/x.html")]
    [InlineData("C:\\Users\\owner\\AppData\\profile", "C:\\Users\\owner\\AppData\\profile")]
    public void Urls_lose_their_query_string_and_fragment_and_everything_else_is_untouched(string input, string expected)
    {
        Assert.Equal(expected, WorkerLogSanitizer.Sanitize(input));
    }

    [Theory]
    [InlineData("authorization: Bearer abc")]
    [InlineData("Authorization=abc")]
    [InlineData("api_key=abc")]
    [InlineData("x-api-key : abc")]
    [InlineData("Set-Cookie: sid=1")]
    [InlineData("password = hunter2")]
    [InlineData("log: user_password: hunter2")]
    [InlineData("csrfToken=abc")]
    [InlineData("client_secret: s")]
    [InlineData("localStorage: {...}")]
    [InlineData("tokens_used=3")]
    public void A_credential_like_key_followed_by_a_separator_replaces_the_whole_line_with_the_marker(string input)
    {
        Assert.Equal(WorkerLogSanitizer.RedactedMarker, WorkerLogSanitizer.Sanitize(input));
    }

    [Theory]
    [InlineData("the cookie banner was dismissed")]
    [InlineData("password field detected on landing page")]
    [InlineData("event=browser.forbidden_key_redacted keys=['cookie']")]
    [InlineData("text_chars=1200 links_count=14")]
    [InlineData("https://example.org/?token=abc")]
    public void A_credential_word_without_a_value_separator_is_kept(string input)
    {
        Assert.NotEqual(WorkerLogSanitizer.RedactedMarker, WorkerLogSanitizer.Sanitize(input));
    }

    [Fact]
    public void Lines_are_capped_at_the_error_message_limit_with_a_visible_cut_marker()
    {
        Assert.Equal(ProtocolConstants.MaxErrorMessageLength, WorkerLogSanitizer.MaxLineChars);

        var exact = new string('x', WorkerLogSanitizer.MaxLineChars);
        Assert.Equal(exact, WorkerLogSanitizer.Sanitize(exact));

        var over = new string('y', WorkerLogSanitizer.MaxLineChars + 7);
        var cut = WorkerLogSanitizer.Sanitize(over);
        Assert.StartsWith(new string('y', WorkerLogSanitizer.MaxLineChars), cut, StringComparison.Ordinal);
        Assert.EndsWith(" …[+7 chars cut]", cut, StringComparison.Ordinal);
        Assert.Equal(WorkerLogSanitizer.MaxLineChars + " …[+7 chars cut]".Length, cut.Length);

        // The bound is what matters: a 1 MiB line is logged as 2000 chars plus the marker.
        var huge = WorkerLogSanitizer.Sanitize(new string('z', 1024 * 1024));
        Assert.True(huge.Length < WorkerLogSanitizer.MaxLineChars + 64, $"length {huge.Length}");
    }

    /// <summary>A read-only stream that counts how many bytes a reader actually pulled.</summary>
    private sealed class CountingStream(byte[] data) : Stream
    {
        private int _position;

        public long BytesRead { get; private set; }

        public override bool CanRead => true;

        public override bool CanSeek => false;

        public override bool CanWrite => false;

        public override long Length => data.Length;

        public override long Position { get => _position; set => throw new NotSupportedException(); }

        public override int Read(byte[] buffer, int offset, int count)
        {
            var n = Math.Min(count, data.Length - _position);
            Array.Copy(data, _position, buffer, offset, n);
            _position += n;
            BytesRead += n;
            return n;
        }

        public override void Flush()
        {
        }

        public override long Seek(long offset, SeekOrigin origin) => throw new NotSupportedException();

        public override void SetLength(long value) => throw new NotSupportedException();

        public override void Write(byte[] buffer, int offset, int count) => throw new NotSupportedException();
    }
}
