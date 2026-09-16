using System.IO.Compression;
using System.Text;
using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>
/// What an Android package says about itself — package name, version code and name, SDK levels,
/// and whether it carries a signature — read from the FILE: the APK's binary manifest
/// (<c>AndroidManifest.xml</c>, AXML) or the bundle's protobuf manifest
/// (<c>base/manifest/AndroidManifest.xml</c>).
/// </summary>
/// <remarks>
/// <para>
/// B49 (ADR-0161). A native build on the device is read back by <c>file.inspect</c>, and the
/// Cloud Core may call it <c>verified</c> only by comparing what the ARTEFACT says with what was
/// asked (<c>app.nativefactory.artifacts.validate_android_against_spec</c>) — never because a
/// build exited 0 and never from a build tool's own metadata file. This is the Android half of
/// <see cref="PeImageReader"/>: an additive <c>android</c> block on a result the device already
/// returns, no new capability name, no new file kind.
/// </para>
/// <para>
/// Bounded and total: the manifest entry is read up to <see cref="MaxManifestBytes"/>, the string
/// pool and the protobuf nesting are capped, and anything unreadable answers <c>null</c> rather
/// than an exception — the caller then reports a file with no identity, which is the truth.
/// </para>
/// </remarks>
public static class AndroidPackageReader
{
    public const string ApkExtension = ".apk";
    public const string BundleExtension = ".aab";
    public const int MaxManifestBytes = 4 * 1024 * 1024;
    public const int MaxStrings = 65536;
    private const int MaxDepth = 32;

    private const string ApkManifest = "AndroidManifest.xml";
    private const string BundleManifest = "base/manifest/AndroidManifest.xml";

    /// <summary>The <c>android</c> block for an APK or AAB, or null when the file is not one this reader can read.</summary>
    public static JsonObject? TryRead(string path)
    {
        var extension = Path.GetExtension(path);
        var isApk = string.Equals(extension, ApkExtension, StringComparison.OrdinalIgnoreCase);
        var isBundle = string.Equals(extension, BundleExtension, StringComparison.OrdinalIgnoreCase);
        if (!isApk && !isBundle)
        {
            return null;
        }

        try
        {
            using var stream = File.OpenRead(path);
            using var zip = new ZipArchive(stream, ZipArchiveMode.Read, leaveOpen: true);
            var entry = zip.GetEntry(isApk ? ApkManifest : BundleManifest);
            if (entry is null || entry.Length <= 0 || entry.Length > MaxManifestBytes)
            {
                return null;
            }

            var bytes = ReadAll(entry);
            var manifest = isApk ? ReadBinaryXml(bytes) : ReadProtoXml(bytes);
            if (manifest is null || string.IsNullOrEmpty(manifest.Package))
            {
                return null;
            }

            var schemes = new JsonArray();
            if (zip.Entries.Any(e => e.FullName.StartsWith("META-INF/", StringComparison.OrdinalIgnoreCase)
                && (e.FullName.EndsWith(".RSA", StringComparison.OrdinalIgnoreCase)
                    || e.FullName.EndsWith(".DSA", StringComparison.OrdinalIgnoreCase)
                    || e.FullName.EndsWith(".EC", StringComparison.OrdinalIgnoreCase))))
            {
                schemes.Add("jar");
            }

            if (isApk && HasSigningBlock(stream))
            {
                schemes.Add("v2+");
            }

            var block = new JsonObject
            {
                ["format"] = isApk ? "apk" : "aab",
                ["package"] = manifest.Package,
                ["version_code"] = manifest.VersionCode,
                ["version_name"] = manifest.VersionName,
                ["min_sdk"] = manifest.MinSdk,
                ["target_sdk"] = manifest.TargetSdk,
                ["signed"] = schemes.Count > 0,
                ["signature_schemes"] = schemes,
            };
            return block;
        }
        catch (Exception)
        {
            return null;
        }
    }

    public sealed record ManifestFacts(string? Package, long? VersionCode, string? VersionName, long? MinSdk, long? TargetSdk);

    private static byte[] ReadAll(ZipArchiveEntry entry)
    {
        using var input = entry.Open();
        using var buffer = new MemoryStream();
        var chunk = new byte[81920];
        int read;
        while ((read = input.Read(chunk, 0, chunk.Length)) > 0)
        {
            if (buffer.Length + read > MaxManifestBytes)
            {
                throw new InvalidDataException("manifest larger than it claimed");
            }

            buffer.Write(chunk, 0, read);
        }

        return buffer.ToArray();
    }

    // ------------------------------------------------------------------ APK: binary XML

    private const ushort ResXmlType = 0x0003;
    private const ushort ResStringPoolType = 0x0001;
    private const ushort ResXmlStartElementType = 0x0102;
    private const byte TypeString = 0x03;
    private const byte TypeIntDec = 0x10;
    private const byte TypeIntHex = 0x11;
    private const uint NoIndex = 0xFFFFFFFF;

    /// <summary>The facts of an AXML document; null when it is not one.</summary>
    public static ManifestFacts? ReadBinaryXml(byte[] data)
    {
        if (data.Length < 8 || U16(data, 0) != ResXmlType)
        {
            return null;
        }

        var end = (int)Math.Min(U32(data, 4), (uint)data.Length);
        int offset = U16(data, 2);
        string[]? strings = null;
        string? package = null, versionName = null;
        long? versionCode = null, minSdk = null, targetSdk = null;

        while (offset + 8 <= end)
        {
            var type = U16(data, offset);
            var headerSize = U16(data, offset + 2);
            var size = (int)U32(data, offset + 4);
            if (size < 8 || offset + size > end)
            {
                return null;
            }

            if (type == ResStringPoolType)
            {
                strings = ReadStringPool(data, offset, headerSize, size);
                if (strings is null)
                {
                    return null;
                }
            }
            else if (type == ResXmlStartElementType && strings is not null)
            {
                var ext = offset + headerSize;
                var name = At(strings, U32(data, ext + 4));
                var attributeStart = U16(data, ext + 8);
                var attributeSize = U16(data, ext + 10);
                var attributeCount = U16(data, ext + 12);
                if (attributeSize < 20)
                {
                    return null;
                }

                for (var i = 0; i < attributeCount; i++)
                {
                    var a = ext + attributeStart + (i * attributeSize);
                    if (a + 20 > offset + size)
                    {
                        return null;
                    }

                    var attribute = At(strings, U32(data, a + 4));
                    var raw = U32(data, a + 8);
                    var dataType = data[a + 15];
                    var value = U32(data, a + 16);
                    string? text = raw != NoIndex ? At(strings, raw) : dataType == TypeString ? At(strings, value) : null;
                    long? number = dataType is TypeIntDec or TypeIntHex ? (long)value : long.TryParse(text, out var parsed) ? parsed : null;

                    switch (name, attribute)
                    {
                        case ("manifest", "package"):
                            package = text;
                            break;
                        case ("manifest", "versionCode"):
                            versionCode = number;
                            break;
                        case ("manifest", "versionName"):
                            versionName = text;
                            break;
                        case ("uses-sdk", "minSdkVersion"):
                            minSdk = number;
                            break;
                        case ("uses-sdk", "targetSdkVersion"):
                            targetSdk = number;
                            break;
                    }
                }
            }

            offset += size;
        }

        return new ManifestFacts(package, versionCode, versionName, minSdk, targetSdk);
    }

    private static string[]? ReadStringPool(byte[] data, int chunk, int headerSize, int size)
    {
        var count = (int)U32(data, chunk + 8);
        var flags = U32(data, chunk + 16);
        var stringsStart = (int)U32(data, chunk + 20);
        if (count < 0 || count > MaxStrings || chunk + headerSize + (count * 4L) > chunk + size)
        {
            return null;
        }

        var utf8 = (flags & 0x100) != 0;
        var result = new string[count];
        for (var i = 0; i < count; i++)
        {
            var at = chunk + stringsStart + (int)U32(data, chunk + headerSize + (i * 4));
            if (at < chunk || at >= chunk + size)
            {
                return null;
            }

            result[i] = utf8 ? Utf8At(data, at, chunk + size) : Utf16At(data, at, chunk + size);
        }

        return result;
    }

    private static string Utf8At(byte[] data, int at, int limit)
    {
        // char count (1 or 2 bytes), then byte count (1 or 2 bytes), then the bytes.
        at += (data[at] & 0x80) != 0 ? 2 : 1;
        var length = data[at] & 0x7F;
        if ((data[at] & 0x80) != 0)
        {
            length = (length << 8) | data[at + 1];
            at += 2;
        }
        else
        {
            at += 1;
        }

        return at + length <= limit ? Encoding.UTF8.GetString(data, at, length) : string.Empty;
    }

    private static string Utf16At(byte[] data, int at, int limit)
    {
        int length = U16(data, at);
        at += 2;
        if ((length & 0x8000) != 0)
        {
            length = ((length & 0x7FFF) << 16) | U16(data, at);
            at += 2;
        }

        return at + (length * 2) <= limit ? Encoding.Unicode.GetString(data, at, length * 2) : string.Empty;
    }

    private static string? At(string[] strings, uint index) => index < strings.Length ? strings[index] : null;

    private static ushort U16(byte[] data, int at)
        => at + 2 <= data.Length ? BitConverter.ToUInt16(data, at) : throw new InvalidDataException("truncated");

    private static uint U32(byte[] data, int at)
        => at + 4 <= data.Length ? BitConverter.ToUInt32(data, at) : throw new InvalidDataException("truncated");

    /// <summary>The APK Signature Scheme v2+ block sits just before the central directory and ends with this magic.</summary>
    private static bool HasSigningBlock(Stream stream)
    {
        // End of central directory: the last 22..65557 bytes; its offset 16 is the central directory's start.
        var tail = (int)Math.Min(stream.Length, 65557);
        var buffer = new byte[tail];
        stream.Seek(-tail, SeekOrigin.End);
        stream.ReadExactly(buffer);
        for (var i = tail - 22; i >= 0; i--)
        {
            if (buffer[i] != 0x50 || buffer[i + 1] != 0x4b || buffer[i + 2] != 0x05 || buffer[i + 3] != 0x06)
            {
                continue;
            }

            long centralDirectory = BitConverter.ToUInt32(buffer, i + 16);
            if (centralDirectory < 24 || centralDirectory > stream.Length)
            {
                return false;
            }

            var magic = new byte[16];
            stream.Seek(centralDirectory - 16, SeekOrigin.Begin);
            stream.ReadExactly(magic);
            return Encoding.ASCII.GetString(magic) == "APK Sig Block 42";
        }

        return false;
    }

    // ------------------------------------------------------------------ AAB: protobuf XML

    /// <summary>The facts of an <c>aapt.pb.XmlNode</c> manifest; null when it is not one.</summary>
    public static ManifestFacts? ReadProtoXml(byte[] data)
    {
        string? package = null, versionName = null;
        long? versionCode = null, minSdk = null, targetSdk = null;
        var elements = 0;

        // XmlNode.element = 1
        void Node(ReadOnlySpan<byte> node, int depth)
        {
            if (depth > MaxDepth)
            {
                throw new InvalidDataException("too deep");
            }

            foreach (var (field, _, bytes) in Fields(node))
            {
                if (field == 1)
                {
                    Element(bytes.Span, depth + 1);
                }
            }
        }

        // XmlElement: name = 3, attribute = 4, child = 5
        void Element(ReadOnlySpan<byte> element, int depth)
        {
            if (++elements > 100_000)
            {
                throw new InvalidDataException("too many elements");
            }

            string? name = null;
            var attributes = new List<ReadOnlyMemory<byte>>();
            var children = new List<ReadOnlyMemory<byte>>();
            foreach (var (field, _, bytes) in Fields(element))
            {
                switch (field)
                {
                    case 3:
                        name = Encoding.UTF8.GetString(bytes.Span);
                        break;
                    case 4:
                        attributes.Add(bytes);
                        break;
                    case 5:
                        children.Add(bytes);
                        break;
                }
            }

            foreach (var attribute in attributes)
            {
                // XmlAttribute: name = 2, value = 3, compiled_item = 6 (Item.prim = 7, Primitive.int_decimal_value = 6)
                string? attributeName = null, value = null;
                long? number = null;
                foreach (var (field, varint, bytes) in Fields(attribute.Span))
                {
                    if (field == 2)
                    {
                        attributeName = Encoding.UTF8.GetString(bytes.Span);
                    }
                    else if (field == 3)
                    {
                        value = Encoding.UTF8.GetString(bytes.Span);
                    }
                    else if (field == 6)
                    {
                        number ??= Primitive(bytes.Span);
                    }

                    _ = varint;
                }

                number ??= long.TryParse(value, out var parsed) ? parsed : null;
                switch (name, attributeName)
                {
                    case ("manifest", "package"):
                        package = value;
                        break;
                    case ("manifest", "versionCode"):
                        versionCode = number;
                        break;
                    case ("manifest", "versionName"):
                        versionName = value;
                        break;
                    case ("uses-sdk", "minSdkVersion"):
                        minSdk = number;
                        break;
                    case ("uses-sdk", "targetSdkVersion"):
                        targetSdk = number;
                        break;
                }
            }

            foreach (var child in children)
            {
                Node(child.Span, depth + 1);
            }
        }

        try
        {
            Node(data, 0);
        }
        catch (Exception)
        {
            return null;
        }

        return elements == 0 ? null : new ManifestFacts(package, versionCode, versionName, minSdk, targetSdk);
    }

    private static long? Primitive(ReadOnlySpan<byte> item)
    {
        foreach (var (field, _, bytes) in Fields(item))
        {
            if (field != 7)
            {
                continue;
            }

            foreach (var (primitiveField, varint, _) in Fields(bytes.Span))
            {
                if (primitiveField is 6 or 7)
                {
                    return (int)varint;
                }
            }
        }

        return null;
    }

    /// <summary>The top-level fields of one protobuf message: (number, varint value, length-delimited bytes).</summary>
    private static List<(int Field, ulong Varint, ReadOnlyMemory<byte> Bytes)> Fields(ReadOnlySpan<byte> message)
    {
        var fields = new List<(int, ulong, ReadOnlyMemory<byte>)>();
        var at = 0;
        while (at < message.Length)
        {
            var key = Varint(message, ref at);
            var field = (int)(key >> 3);
            switch (key & 7)
            {
                case 0:
                    fields.Add((field, Varint(message, ref at), ReadOnlyMemory<byte>.Empty));
                    break;
                case 1:
                    at += 8;
                    break;
                case 2:
                    {
                        var length = Varint(message, ref at);
                        if (length > (ulong)(message.Length - at))
                        {
                            throw new InvalidDataException("length past the end");
                        }

                        fields.Add((field, 0, message.Slice(at, (int)length).ToArray()));
                        at += (int)length;
                        break;
                    }

                case 5:
                    at += 4;
                    break;
                default:
                    throw new InvalidDataException("unsupported wire type");
            }

            if (at > message.Length)
            {
                throw new InvalidDataException("truncated");
            }
        }

        return fields;
    }

    private static ulong Varint(ReadOnlySpan<byte> data, ref int at)
    {
        ulong value = 0;
        for (var shift = 0; shift < 64; shift += 7)
        {
            if (at >= data.Length)
            {
                throw new InvalidDataException("truncated varint");
            }

            var b = data[at++];
            value |= (ulong)(b & 0x7F) << shift;
            if ((b & 0x80) == 0)
            {
                return value;
            }
        }

        throw new InvalidDataException("varint too long");
    }
}
