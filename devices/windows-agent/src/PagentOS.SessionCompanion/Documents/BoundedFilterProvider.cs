using System.IO.Compression;
using UglyToad.PdfPig.Filters;
using UglyToad.PdfPig.Tokens;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// Raised inside PdfPig, from a filter, when a stream would inflate past a bound; caught
/// by <see cref="PdfPigExtractor"/> and answered as <c>unsupported_format</c> /
/// <c>decompression_bound</c>. The provider also remembers the refusal
/// (<see cref="BoundedFilterProvider.Refusal"/>) in case PdfPig's lenient parsing swallows
/// the exception on some path — a tripped bound is never an empty page.
/// </summary>
public sealed class DecompressionBoundException(string message) : Exception(message);

/// <summary>
/// PdfPig's one seam for decompression (<c>ParsingOptions.FilterProvider</c>): every stream
/// the library decodes — the cross-reference stream at open, object streams, a page's
/// content, a font program, a form XObject — asks this provider for its filters. The default
/// filters are kept (the bytes PdfPig sees are exactly the default's) but the three that
/// can amplify are wrapped: before the inner filter materialises its output, the wrapper
/// COUNTS what that output would be in a streaming pass with a 64 KiB scratch buffer —
/// Flate through the runtime's own inflater, LZW and RunLength through length-only
/// decoders that mirror the PDF specification (7.4.4.2, 7.4.5) — and refuses at
/// <see cref="DocumentBounds.MaxPdfStreamBytes"/> per stream and
/// <see cref="DocumentBounds.MaxPdfInflatedBytes"/> per document. A stream the counter
/// cannot follow (corrupt data) counts what it could and lets the inner filter answer, as
/// the inner filter would have produced no more than that from the same bytes. The image
/// codecs (DCT, JPX, JBIG2, CCITT) are not wrapped: PdfPig decodes them only on
/// <c>GetImages()</c>, which the extractor never calls.
/// </summary>
public sealed class BoundedFilterProvider : IFilterProvider
{
    private readonly IFilterProvider _inner;
    private readonly long _perStream;
    private readonly long _perDocument;
    private long _inflated;

    public BoundedFilterProvider(long perStream = DocumentBounds.MaxPdfStreamBytes, long perDocument = DocumentBounds.MaxPdfInflatedBytes, IFilterProvider? inner = null)
    {
        _inner = inner ?? DefaultFilterProvider.Instance;
        _perStream = perStream;
        _perDocument = perDocument;
    }

    /// <summary>The inflated bytes counted so far across this document.</summary>
    public long Inflated => _inflated;

    /// <summary>How many streams were counted.</summary>
    public int Decodes { get; private set; }

    /// <summary>Set once a bound tripped; the reason with the numbers.</summary>
    public string? Refusal { get; private set; }

    public bool Tripped => Refusal is not null;

    public IReadOnlyList<IFilter> GetFilters(DictionaryToken dictionary) => Wrap(_inner.GetFilters(dictionary));

    public IReadOnlyList<IFilter> GetNamedFilters(IReadOnlyList<NameToken> names) => Wrap(_inner.GetNamedFilters(names));

    public IReadOnlyList<IFilter> GetAllFilters() => Wrap(_inner.GetAllFilters());

    private IReadOnlyList<IFilter> Wrap(IReadOnlyList<IFilter> filters)
    {
        var wrapped = new List<IFilter>(filters.Count);
        foreach (var filter in filters)
        {
            wrapped.Add(filter switch
            {
                FlateFilter => new Bounded(filter, this, static (input, _, cap) => CountFlate(input.Span, cap)),
                LzwFilter => new Bounded(filter, this, static (input, parameters, cap) => CountLzw(input.Span, EarlyChange(parameters), cap)),
                RunLengthFilter => new Bounded(filter, this, static (input, _, cap) => CountRunLength(input.Span, cap)),
                _ => filter,
            });
        }

        return wrapped;
    }

    private void Account(long count, int rawLength)
    {
        Decodes++;
        if (count > _perStream)
        {
            Refusal ??= $"a stream of {rawLength} bytes inflates past the {DocumentBounds.Mebibytes(_perStream)} per-stream bound";
            throw new DecompressionBoundException(Refusal);
        }

        _inflated += count;
        if (_inflated > _perDocument)
        {
            Refusal ??= $"the document's streams inflate past the {DocumentBounds.Mebibytes(_perDocument)} per-document bound";
            throw new DecompressionBoundException(Refusal);
        }
    }

    private static int EarlyChange(DictionaryToken? parameters)
    {
        if (parameters is not null && parameters.TryGet(NameToken.Create("EarlyChange"), out var token) && token is NumericToken numeric)
        {
            return numeric.Int == 0 ? 0 : 1;
        }

        return 1;
    }

    // ================================================================== counters

    /// <summary>
    /// The inflated length of a Flate stream, capped: stops (returning <c>cap + 1</c>) as
    /// soon as the output passes <paramref name="cap"/>. PDF Flate streams carry a zlib
    /// header the runtime's <see cref="DeflateStream"/> does not read; the inner filter may
    /// skip it, parse it or try raw deflate, so all three readings are counted and the
    /// largest kept — the bound holds whichever the inner filter chooses.
    /// </summary>
    public static long CountFlate(ReadOnlySpan<byte> input, long cap)
    {
        var bytes = input.ToArray();
        var best = 0L;
        foreach (var reading in new Func<byte[], Stream>[]
        {
            static b => new DeflateStream(new MemoryStream(b, b.Length > 2 ? 2 : 0, Math.Max(0, b.Length - (b.Length > 2 ? 2 : 0))), CompressionMode.Decompress),
            static b => new ZLibStream(new MemoryStream(b), CompressionMode.Decompress),
            static b => new DeflateStream(new MemoryStream(b), CompressionMode.Decompress),
        })
        {
            var count = CountStream(bytes, reading, cap);
            best = Math.Max(best, count);
            if (best > cap)
            {
                return best;
            }
        }

        return best;
    }

    private static long CountStream(byte[] bytes, Func<byte[], Stream> open, long cap)
    {
        var scratch = new byte[64 * 1024];
        long total = 0;
        try
        {
            using var stream = open(bytes);
            int read;
            while ((read = stream.Read(scratch, 0, scratch.Length)) > 0)
            {
                total += read;
                if (total > cap)
                {
                    return cap + 1;
                }
            }
        }
        catch (Exception ex) when (ex is InvalidDataException or IOException or ArgumentException)
        {
            // Corrupt or not this framing: what was counted is what a decoder could produce.
        }

        return total;
    }

    /// <summary>
    /// The decoded length of a PDF LZW stream (7.4.4.2) without building a byte of it: the
    /// dictionary is kept as entry LENGTHS only (an entry is its predecessor plus one byte),
    /// codes are 9–12 bits MSB-first, 256 clears, 257 ends, the width grows one code early
    /// when <paramref name="earlyChange"/> is 1 (the default). A code the table cannot have
    /// stops the count where the specification's decoder would stop.
    /// </summary>
    public static long CountLzw(ReadOnlySpan<byte> input, int earlyChange, long cap)
    {
        var lengths = new int[4096];
        for (var i = 0; i < 256; i++)
        {
            lengths[i] = 1;
        }

        var width = 9;
        var next = 258;
        var previous = -1;
        long total = 0;
        long bitBuffer = 0;
        var bitCount = 0;
        var position = 0;

        while (true)
        {
            while (bitCount < width && position < input.Length)
            {
                bitBuffer = (bitBuffer << 8) | input[position++];
                bitCount += 8;
            }

            if (bitCount < width)
            {
                break;
            }

            var code = (int)((bitBuffer >> (bitCount - width)) & ((1 << width) - 1));
            bitCount -= width;

            if (code == 256)
            {
                width = 9;
                next = 258;
                previous = -1;
                continue;
            }

            if (code == 257)
            {
                break;
            }

            int emitted;
            if (previous == -1)
            {
                if (code >= 258)
                {
                    break;
                }

                emitted = lengths[code];
            }
            else if (code < next)
            {
                emitted = lengths[code];
                if (next < 4096)
                {
                    lengths[next++] = lengths[previous] + 1;
                }
            }
            else if (code == next && next < 4096)
            {
                emitted = lengths[previous] + 1;
                lengths[next++] = emitted;
            }
            else
            {
                break;
            }

            total += emitted;
            if (total > cap)
            {
                return cap + 1;
            }

            previous = code;
            if (next + earlyChange >= (1 << width) && width < 12)
            {
                width++;
            }
        }

        return total;
    }

    /// <summary>The decoded length of a RunLength stream (7.4.5): a length byte L below 128 copies L+1 bytes, above 128 repeats one byte 257−L times, 128 ends.</summary>
    public static long CountRunLength(ReadOnlySpan<byte> input, long cap)
    {
        long total = 0;
        var position = 0;
        while (position < input.Length)
        {
            int length = input[position++];
            if (length == 128)
            {
                break;
            }

            if (length < 128)
            {
                total += length + 1;
                position += length + 1;
            }
            else
            {
                total += 257 - length;
                position++;
            }

            if (total > cap)
            {
                return cap + 1;
            }
        }

        return total;
    }

    private sealed class Bounded(IFilter inner, BoundedFilterProvider owner, Func<Memory<byte>, DictionaryToken?, long, long> count) : IFilter
    {
        public bool IsSupported => inner.IsSupported;

        public Memory<byte> Decode(Memory<byte> input, DictionaryToken streamDictionary, IFilterProvider filterProvider, int filterIndex)
        {
            DictionaryToken? parameters = null;
            try
            {
                parameters = DecodeParameterResolver.GetFilterParameters(streamDictionary, filterIndex);
            }
            catch (Exception)
            {
                // No parameters: the defaults apply to the count; the inner filter decides for itself.
            }

            owner.Account(count(input, parameters, owner._perStream), input.Length);
            return inner.Decode(input, streamDictionary, filterProvider, filterIndex);
        }
    }
}
