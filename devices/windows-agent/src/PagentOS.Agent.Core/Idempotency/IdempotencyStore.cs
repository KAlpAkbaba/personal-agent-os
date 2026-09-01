using System.Text.Json;
using System.Text.Json.Nodes;
using PagentOS.Agent.Core.Protocol;

namespace PagentOS.Agent.Core.Idempotency;

/// <summary>
/// Persistent bounded map idempotency_key -> terminal command_ack (DEVICE_PROTOCOL.md §5).
/// LRU-bounded (default 1000 entries), stored as a single JSON file with atomic replace writes,
/// survives restarts. Reads refresh in-memory recency; the file is rewritten on every put.
/// </summary>
public sealed class IdempotencyStore
{
    public const int DefaultCapacity = 1000;

    private readonly object _sync = new();
    private readonly string _path;
    private readonly int _capacity;
    private readonly Dictionary<string, LinkedListNode<Entry>> _byKey = new(StringComparer.Ordinal);
    private readonly Dictionary<string, LinkedListNode<Entry>> _byCommandId = new(StringComparer.OrdinalIgnoreCase);
    private readonly LinkedList<Entry> _lru = new(); // head = oldest, tail = most recent

    private sealed record Entry(string IdempotencyKey, string CommandId, string AckJson);

    public IdempotencyStore(string path, int capacity = DefaultCapacity)
    {
        ArgumentOutOfRangeException.ThrowIfLessThan(capacity, 1);
        _path = path;
        _capacity = capacity;
        Load();
    }

    public int Count
    {
        get
        {
            lock (_sync)
            {
                return _lru.Count;
            }
        }
    }

    public bool TryGetTerminalAck(string idempotencyKey, out CommandAckMessage? ack)
    {
        lock (_sync)
        {
            if (_byKey.TryGetValue(idempotencyKey, out var node))
            {
                Touch(node);
                ack = (CommandAckMessage)ProtocolJson.Deserialize(node.Value.AckJson);
                return true;
            }
        }

        ack = null;
        return false;
    }

    public bool TryGetAckByCommandId(string commandId, out CommandAckMessage? ack)
    {
        lock (_sync)
        {
            if (_byCommandId.TryGetValue(commandId, out var node))
            {
                Touch(node);
                ack = (CommandAckMessage)ProtocolJson.Deserialize(node.Value.AckJson);
                return true;
            }
        }

        ack = null;
        return false;
    }

    public void PutTerminalAck(string idempotencyKey, CommandAckMessage terminalAck)
    {
        if (!AckStatus.IsTerminal(terminalAck.Status))
        {
            throw new ArgumentException($"ack status '{terminalAck.Status}' is not terminal", nameof(terminalAck));
        }

        var entry = new Entry(idempotencyKey, terminalAck.CommandId, ProtocolJson.Serialize(terminalAck));
        lock (_sync)
        {
            if (_byKey.TryGetValue(idempotencyKey, out var existing))
            {
                _byCommandId.Remove(existing.Value.CommandId);
                _lru.Remove(existing);
                _byKey.Remove(idempotencyKey);
            }

            var node = _lru.AddLast(entry);
            _byKey[idempotencyKey] = node;
            _byCommandId[entry.CommandId] = node;

            while (_lru.Count > _capacity)
            {
                var oldest = _lru.First!;
                _lru.RemoveFirst();
                _byKey.Remove(oldest.Value.IdempotencyKey);
                _byCommandId.Remove(oldest.Value.CommandId);
            }

            Persist();
        }
    }

    private void Touch(LinkedListNode<Entry> node)
    {
        _lru.Remove(node);
        _lru.AddLast(node);
    }

    private void Load()
    {
        if (!File.Exists(_path))
        {
            return;
        }

        JsonNode? root;
        try
        {
            root = JsonNode.Parse(File.ReadAllText(_path));
        }
        catch (Exception)
        {
            // Corrupt store: preserve the broken file for diagnosis and start empty.
            try
            {
                File.Move(_path, _path + ".corrupt", overwrite: true);
            }
            catch (Exception)
            {
                // Ignore; an unreadable store must never block the agent.
            }

            return;
        }

        if (root?["entries"] is not JsonArray entries)
        {
            return;
        }

        foreach (var item in entries)
        {
            var key = item?["idempotency_key"]?.GetValue<string>();
            var commandId = item?["command_id"]?.GetValue<string>();
            var ackNode = item?["ack"];
            if (key is null || commandId is null || ackNode is null)
            {
                continue;
            }

            var node = _lru.AddLast(new Entry(key, commandId, ackNode.ToJsonString()));
            _byKey[key] = node;
            _byCommandId[commandId] = node;
        }

        while (_lru.Count > _capacity)
        {
            var oldest = _lru.First!;
            _lru.RemoveFirst();
            _byKey.Remove(oldest.Value.IdempotencyKey);
            _byCommandId.Remove(oldest.Value.CommandId);
        }
    }

    private void Persist()
    {
        var entries = new JsonArray();
        foreach (var entry in _lru)
        {
            entries.Add(new JsonObject
            {
                ["idempotency_key"] = entry.IdempotencyKey,
                ["command_id"] = entry.CommandId,
                ["ack"] = JsonNode.Parse(entry.AckJson),
            });
        }

        var root = new JsonObject { ["version"] = 1, ["entries"] = entries };
        var directory = Path.GetDirectoryName(Path.GetFullPath(_path));
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var tempPath = _path + ".tmp";
        File.WriteAllText(tempPath, root.ToJsonString(new JsonSerializerOptions { WriteIndented = false }));
        File.Move(tempPath, _path, overwrite: true);
        // Machine state, protected after the move for the same reason as the key and the
        // enrollment state: the final DACL excludes the writing account.
        Security.MachineMaterial.Protect(_path, Security.MachineMaterialKind.State);
    }
}
