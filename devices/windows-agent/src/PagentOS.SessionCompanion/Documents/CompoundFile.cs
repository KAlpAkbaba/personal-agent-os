using System.Text;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// B52 (req 146): a bounded reader for the OLE compound file ([MS-CFB]) the legacy Office
/// formats live in. It reads streams by name and nothing else: no writing, no storage
/// traversal beyond the flat directory, no stream larger than <see cref="MaxStreamBytes"/>.
/// Every sector chain is walked with a step bound equal to the number of sectors the file can
/// hold, so a crafted loop ends in <c>parse_failed</c> instead of a hang.
/// </summary>
public sealed class CompoundFile
{
    public static readonly byte[] Signature = [0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1];

    /// <summary>The largest stream this reader materialises.</summary>
    public const long MaxStreamBytes = DocumentBounds.MaxPackageInflatedBytes;

    /// <summary>The most directory entries read.</summary>
    public const int MaxDirectoryEntries = 100_000;

    private const uint EndOfChain = 0xFFFFFFFE;
    private const uint FreeSector = 0xFFFFFFFF;
    private const int HeaderDifatEntries = 109;

    private readonly byte[] _data;
    private readonly int _sectorSize;
    private readonly int _miniSectorSize;
    private readonly uint _miniStreamCutoff;
    private readonly uint[] _fat;
    private readonly uint[] _miniFat;
    private readonly List<Entry> _entries;
    private readonly byte[] _miniStream;

    public sealed record Entry(string Name, byte Type, uint StartSector, long Size);

    private CompoundFile(byte[] data)
    {
        _data = data;
        if (data.Length < 512 || !data.AsSpan(0, 8).SequenceEqual(Signature))
        {
            throw Fail("the file is not an OLE compound document (signature)");
        }

        var sectorShift = BitConverter.ToUInt16(data, 30);
        var miniShift = BitConverter.ToUInt16(data, 32);
        if (sectorShift is not (9 or 12) || miniShift != 6)
        {
            throw Fail($"unsupported sector shift {sectorShift}/{miniShift}");
        }

        _sectorSize = 1 << sectorShift;
        _miniSectorSize = 1 << miniShift;
        var fatSectors = BitConverter.ToUInt32(data, 44);
        var firstDirectory = BitConverter.ToUInt32(data, 48);
        _miniStreamCutoff = BitConverter.ToUInt32(data, 56);
        var firstMiniFat = BitConverter.ToUInt32(data, 60);
        var miniFatSectors = BitConverter.ToUInt32(data, 64);
        var firstDifat = BitConverter.ToUInt32(data, 68);
        var difatSectors = BitConverter.ToUInt32(data, 72);

        var sectorCount = Math.Max(0, (data.Length - _sectorSize) / _sectorSize);
        if (fatSectors > sectorCount || miniFatSectors > sectorCount || difatSectors > sectorCount)
        {
            throw Fail("the header claims more sectors than the file holds");
        }

        // DIFAT: 109 entries in the header, then a chain of DIFAT sectors.
        var fatSectorIds = new List<uint>();
        for (var i = 0; i < HeaderDifatEntries && fatSectorIds.Count < fatSectors; i++)
        {
            var id = BitConverter.ToUInt32(data, 76 + i * 4);
            if (id != FreeSector)
            {
                fatSectorIds.Add(id);
            }
        }

        var difat = firstDifat;
        var steps = 0;
        while (difat != EndOfChain && difat != FreeSector && fatSectorIds.Count < fatSectors)
        {
            if (++steps > sectorCount)
            {
                throw Fail("the DIFAT chain loops");
            }

            var offset = SectorOffset(difat);
            var perSector = _sectorSize / 4 - 1;
            for (var i = 0; i < perSector && fatSectorIds.Count < fatSectors; i++)
            {
                var id = BitConverter.ToUInt32(data, offset + i * 4);
                if (id != FreeSector)
                {
                    fatSectorIds.Add(id);
                }
            }

            difat = BitConverter.ToUInt32(data, offset + perSector * 4);
        }

        var fat = new List<uint>(fatSectorIds.Count * (_sectorSize / 4));
        foreach (var id in fatSectorIds)
        {
            var offset = SectorOffset(id);
            for (var i = 0; i < _sectorSize / 4; i++)
            {
                fat.Add(BitConverter.ToUInt32(data, offset + i * 4));
            }
        }

        _fat = [.. fat];
        _miniFat = ToUInts(ReadChain(firstMiniFat, (long)miniFatSectors * _sectorSize));

        var directory = ReadChain(firstDirectory, long.MaxValue);
        _entries = new List<Entry>();
        for (var offset = 0; offset + 128 <= directory.Length && _entries.Count < MaxDirectoryEntries; offset += 128)
        {
            var nameLength = BitConverter.ToUInt16(directory, offset + 64);
            var type = directory[offset + 66];
            var name = nameLength is >= 2 and <= 64 ? Encoding.Unicode.GetString(directory, offset, nameLength - 2) : string.Empty;
            var start = BitConverter.ToUInt32(directory, offset + 116);
            long size = BitConverter.ToUInt32(directory, offset + 120);
            if (_sectorSize == 4096)
            {
                size |= (long)BitConverter.ToUInt32(directory, offset + 124) << 32;
            }

            _entries.Add(new Entry(name, type, start, size));
        }

        var root = _entries.FirstOrDefault(e => e.Type == 5) ?? throw Fail("no root entry");
        _miniStream = root.Size > 0 ? ReadChain(root.StartSector, root.Size) : [];
    }

    public static CompoundFile Open(string path)
    {
        var info = new FileInfo(path);
        if (info.Length > PagentOS.Agent.Core.Protocol.DocumentCapabilityNames.MaxFileBytes)
        {
            throw DocumentErrors.Unsupported($"'{info.Name}' is larger than the document bound", DocumentErrors.TooLarge);
        }

        return new CompoundFile(File.ReadAllBytes(path));
    }

    public static CompoundFile FromBytes(byte[] data) => new(data);

    public static bool LooksLikeCompoundFile(string path)
    {
        using var stream = File.OpenRead(path);
        var head = new byte[8];
        return stream.Read(head, 0, 8) == 8 && head.AsSpan().SequenceEqual(Signature);
    }

    public IReadOnlyList<Entry> Entries => _entries;

    public bool Has(string name) => _entries.Any(e => e.Type == 2 && e.Name.Equals(name, StringComparison.OrdinalIgnoreCase));

    public byte[]? TryRead(string name)
    {
        var entry = _entries.FirstOrDefault(e => e.Type == 2 && e.Name.Equals(name, StringComparison.OrdinalIgnoreCase));
        if (entry is null)
        {
            return null;
        }

        if (entry.Size > MaxStreamBytes)
        {
            throw DocumentErrors.Unsupported($"stream '{name}' is {entry.Size} bytes, over the {DocumentBounds.Mebibytes(MaxStreamBytes)} bound", DocumentErrors.DecompressionBound);
        }

        return entry.Size < _miniStreamCutoff ? ReadMiniChain(entry.StartSector, entry.Size) : ReadChain(entry.StartSector, entry.Size);
    }

    private int SectorOffset(uint sector)
    {
        var offset = ((long)sector + 1) * _sectorSize;
        if (offset + _sectorSize > _data.Length)
        {
            throw Fail($"sector {sector} lies outside the file");
        }

        return (int)offset;
    }

    private byte[] ReadChain(uint start, long size)
    {
        var buffer = new MemoryStream();
        var sector = start;
        var steps = 0;
        var sectorCount = _data.Length / _sectorSize;
        while (sector != EndOfChain && sector != FreeSector)
        {
            if (++steps > sectorCount || sector >= _fat.Length)
            {
                throw Fail("a sector chain loops or leaves the allocation table");
            }

            buffer.Write(_data, SectorOffset(sector), _sectorSize);
            if (buffer.Length >= size || buffer.Length > MaxStreamBytes)
            {
                break;
            }

            sector = _fat[sector];
        }

        var bytes = buffer.ToArray();
        return size < bytes.Length ? bytes[..(int)size] : bytes;
    }

    private byte[] ReadMiniChain(uint start, long size)
    {
        var buffer = new MemoryStream();
        var sector = start;
        var steps = 0;
        var limit = _miniStream.Length / _miniSectorSize;
        while (sector != EndOfChain && sector != FreeSector)
        {
            if (++steps > limit + 1 || sector >= _miniFat.Length)
            {
                throw Fail("a mini sector chain loops or leaves the mini allocation table");
            }

            var offset = (long)sector * _miniSectorSize;
            if (offset + _miniSectorSize > _miniStream.Length)
            {
                throw Fail("a mini sector lies outside the mini stream");
            }

            buffer.Write(_miniStream, (int)offset, _miniSectorSize);
            if (buffer.Length >= size)
            {
                break;
            }

            sector = _miniFat[sector];
        }

        var bytes = buffer.ToArray();
        return size < bytes.Length ? bytes[..(int)size] : bytes;
    }

    private static uint[] ToUInts(byte[] bytes)
    {
        var result = new uint[bytes.Length / 4];
        for (var i = 0; i < result.Length; i++)
        {
            result[i] = BitConverter.ToUInt32(bytes, i * 4);
        }

        return result;
    }

    private static Exception Fail(string message) => DocumentErrors.Unsupported(message, DocumentErrors.ParseFailed);

    /// <summary>
    /// The title from a <c>\u0005SummaryInformation</c> property set (PIDSI_TITLE = 2), in the
    /// set's own code page; null when absent or unreadable.
    /// </summary>
    public string? SummaryTitle()
    {
        var bytes = TryRead("\u0005SummaryInformation");
        if (bytes is null || bytes.Length < 48)
        {
            return null;
        }

        try
        {
            var sectionOffset = (int)BitConverter.ToUInt32(bytes, 44);
            var count = (int)BitConverter.ToUInt32(bytes, sectionOffset + 4);
            var codePage = 1252;
            string? raw = null;
            byte[]? titleBytes = null;
            for (var i = 0; i < Math.Min(count, 256); i++)
            {
                var id = BitConverter.ToUInt32(bytes, sectionOffset + 8 + i * 8);
                var offset = sectionOffset + (int)BitConverter.ToUInt32(bytes, sectionOffset + 12 + i * 8);
                var type = BitConverter.ToUInt32(bytes, offset);
                if (id == 1 && type == 2)
                {
                    codePage = BitConverter.ToUInt16(bytes, offset + 4);
                }
                else if (id == 2 && type == 0x1E)
                {
                    var length = (int)BitConverter.ToUInt32(bytes, offset + 4);
                    titleBytes = bytes[(offset + 8)..(offset + 8 + Math.Max(0, length))];
                }
                else if (id == 2 && type == 0x1F)
                {
                    var length = (int)BitConverter.ToUInt32(bytes, offset + 4);
                    raw = Encoding.Unicode.GetString(bytes, offset + 8, Math.Max(0, length * 2));
                }
            }

            if (titleBytes is not null)
            {
                Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
                raw = (codePage == 1200 ? Encoding.Unicode : Encoding.GetEncoding(codePage == 65001 ? 65001 : codePage)).GetString(titleBytes);
            }

            var title = raw?.TrimEnd('\0').Trim();
            return string.IsNullOrEmpty(title) ? null : title;
        }
        catch (Exception ex) when (ex is ArgumentException or IndexOutOfRangeException or NotSupportedException)
        {
            return null;
        }
    }
}
