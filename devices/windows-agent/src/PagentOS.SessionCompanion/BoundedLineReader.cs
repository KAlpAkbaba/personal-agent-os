using System.Text;

namespace PagentOS.SessionCompanion;

/// <summary>
/// A newline-delimited reader with a ceiling on the length of one line.
///
/// <see cref="StreamReader.ReadLineAsync()"/> keeps accumulating until it sees a newline,
/// so one pathological (or hostile) line on the worker's stdout would grow without bound
/// inside the companion — the process that also carries <c>desktop.*</c> and voice, whose
/// fate would then be shared with a misbehaving child. This reader stops at
/// <see cref="MaxLineBytes"/> and throws <see cref="LineTooLongException"/>; the host
/// treats that as the worker having broken the protocol and kills it.
///
/// Byte-based on purpose: the ceiling is expressed in bytes (a multiple of the contract's
/// result cap) and applies before any decoding. Lines are UTF-8; a trailing <c>\r</c> is
/// dropped; the last line of a stream that ends without a newline is still returned.
/// </summary>
public sealed class BoundedLineReader
{
    private const int ChunkSize = 16 * 1024;

    private readonly Stream _stream;
    private readonly byte[] _chunk = new byte[ChunkSize];
    private readonly MemoryStream _line = new();
    private int _chunkStart;
    private int _chunkEnd;
    private bool _eof;

    public BoundedLineReader(Stream stream, int maxLineBytes)
    {
        ArgumentOutOfRangeException.ThrowIfLessThanOrEqual(maxLineBytes, 0);
        _stream = stream;
        MaxLineBytes = maxLineBytes;
    }

    /// <summary>The longest line, in bytes (newline excluded), this reader will return.</summary>
    public int MaxLineBytes { get; }

    /// <summary>
    /// The next line without its terminator, or null at end of stream. Throws
    /// <see cref="LineTooLongException"/> as soon as the line being assembled exceeds
    /// <see cref="MaxLineBytes"/> — without reading the rest of it.
    /// </summary>
    public async Task<string?> ReadLineAsync(CancellationToken cancellationToken)
    {
        _line.SetLength(0);
        while (true)
        {
            if (_chunkStart == _chunkEnd)
            {
                if (_eof)
                {
                    return _line.Length == 0 ? null : Finish();
                }

                var read = await _stream.ReadAsync(_chunk.AsMemory(), cancellationToken).ConfigureAwait(false);
                if (read == 0)
                {
                    _eof = true;
                    continue;
                }

                _chunkStart = 0;
                _chunkEnd = read;
            }

            var newline = Array.IndexOf(_chunk, (byte)'\n', _chunkStart, _chunkEnd - _chunkStart);
            var take = (newline < 0 ? _chunkEnd : newline) - _chunkStart;
            if (_line.Length + take > MaxLineBytes)
            {
                var seen = _line.Length + take;
                _line.SetLength(0);
                // Drop what was consumed; the caller is expected to abandon the stream.
                _chunkStart = _chunkEnd;
                throw new LineTooLongException(MaxLineBytes, seen);
            }

            _line.Write(_chunk, _chunkStart, take);
            if (newline < 0)
            {
                _chunkStart = _chunkEnd;
                continue;
            }

            _chunkStart = newline + 1;
            return Finish();
        }
    }

    private string Finish()
    {
        var length = (int)_line.Length;
        var buffer = _line.GetBuffer();
        if (length > 0 && buffer[length - 1] == (byte)'\r')
        {
            length--;
        }

        return Encoding.UTF8.GetString(buffer, 0, length);
    }
}

/// <summary>A line on the stream exceeded the reader's ceiling; the stream is no longer in a usable state.</summary>
public sealed class LineTooLongException(int maxLineBytes, long seenBytes)
    : IOException($"line exceeds {maxLineBytes} bytes (at least {seenBytes} seen)")
{
    public int MaxLineBytes { get; } = maxLineBytes;

    public long SeenBytes { get; } = seenBytes;
}
