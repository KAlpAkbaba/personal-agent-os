using System.Text.Json.Nodes;

namespace PagentOS.SessionCompanion.Documents;

/// <summary>The four reference lists a comparison answers with (§2 <c>file.compare</c>).</summary>
public sealed record RefDiff(
    IReadOnlyList<string> Changed,
    IReadOnlyList<string> Unchanged,
    IReadOnlyList<string> Added,
    IReadOnlyList<string> Removed)
{
    public string Summary => $"changed={Changed.Count} unchanged={Unchanged.Count} added={Added.Count} removed={Removed.Count}";

    public JsonObject WriteTo(JsonObject target)
    {
        target["changed_refs"] = new JsonArray([.. Changed.Select(r => (JsonNode?)r)]);
        target["unchanged_refs"] = new JsonArray([.. Unchanged.Select(r => (JsonNode?)r)]);
        target["added_refs"] = new JsonArray([.. Added.Select(r => (JsonNode?)r)]);
        target["removed_refs"] = new JsonArray([.. Removed.Select(r => (JsonNode?)r)]);
        target["summary"] = Summary;
        return target;
    }
}

/// <summary>
/// §2 <c>file.compare</c>: two documents of one extractable kind are compared BLOCK BY REF —
/// a ref present in both with different normalised text is changed, with the same text
/// unchanged, only in B added, only in A removed, in A's order — so "Madde 3 changed" is
/// <c>p8</c> on both sides and the answer can quote both texts. Two text-like files are
/// compared line by line under <c>L&lt;n&gt;</c>. Whether the CONTENT is the same is never
/// inferred from the diff: it is the equality of the two <c>doc_id</c>s.
/// </summary>
public static class DocumentCompare
{
    public static RefDiff Blocks(IReadOnlyList<DocumentBlock> a, IReadOnlyList<DocumentBlock> b)
    {
        var bByRef = new Dictionary<string, string>(StringComparer.Ordinal);
        var bOrder = new List<string>();
        foreach (var block in b)
        {
            if (bByRef.TryAdd(block.Ref, TextFileReader.Normalise(block.Text)))
            {
                bOrder.Add(block.Ref);
            }
        }

        var changed = new List<string>();
        var unchanged = new List<string>();
        var removed = new List<string>();
        var seenInA = new HashSet<string>(StringComparer.Ordinal);
        foreach (var block in a)
        {
            if (!seenInA.Add(block.Ref))
            {
                continue;
            }

            if (!bByRef.TryGetValue(block.Ref, out var other))
            {
                removed.Add(block.Ref);
            }
            else if (string.Equals(TextFileReader.Normalise(block.Text), other, StringComparison.Ordinal))
            {
                unchanged.Add(block.Ref);
            }
            else
            {
                changed.Add(block.Ref);
            }
        }

        var added = bOrder.Where(r => !seenInA.Contains(r)).ToList();
        return new RefDiff(changed, unchanged, added, removed);
    }

    public static RefDiff Lines(string a, string b)
    {
        var aLines = TextFileReader.SplitLines(a);
        var bLines = TextFileReader.SplitLines(b);
        var changed = new List<string>();
        var unchanged = new List<string>();
        var added = new List<string>();
        var removed = new List<string>();
        var common = Math.Min(aLines.Count, bLines.Count);
        for (var i = 0; i < common; i++)
        {
            var reference = $"L{i + 1}";
            if (string.Equals(TextFileReader.Normalise(aLines[i]), TextFileReader.Normalise(bLines[i]), StringComparison.Ordinal))
            {
                unchanged.Add(reference);
            }
            else
            {
                changed.Add(reference);
            }
        }

        for (var i = common; i < bLines.Count; i++)
        {
            added.Add($"L{i + 1}");
        }

        for (var i = common; i < aLines.Count; i++)
        {
            removed.Add($"L{i + 1}");
        }

        return new RefDiff(changed, unchanged, added, removed);
    }
}
