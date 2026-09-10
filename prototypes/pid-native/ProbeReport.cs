// ProbeReport — deterministic JSON evidence writer for native PID experiments.
// Wing: code | Topic: semantic-state-n2 | Updated: 2026-09-10 14:00

using System.Reflection;
using System.Text.Json;

namespace CDT.AutoCAD.PidProbe;

internal static class ProbeReport
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        WriteIndented = true,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    internal static string ReportDirectory
    {
        get
        {
            string assemblyDirectory = Path.GetDirectoryName(
                Assembly.GetExecutingAssembly().Location
            ) ?? Environment.CurrentDirectory;
            string path = Path.Combine(assemblyDirectory, "reports");
            Directory.CreateDirectory(path);
            return path;
        }
    }

    internal static string Write(string probeId, object payload)
    {
        string path = Path.Combine(ReportDirectory, $"{probeId}.json");
        var envelope = new
        {
            probe_id = probeId,
            timestamp_utc = DateTimeOffset.UtcNow,
            auto_cad_process_id = Environment.ProcessId,
            payload,
        };
        string temporary = path + ".tmp";
        File.WriteAllText(temporary, JsonSerializer.Serialize(envelope, JsonOptions));
        File.Move(temporary, path, true);
        return path;
    }
}
