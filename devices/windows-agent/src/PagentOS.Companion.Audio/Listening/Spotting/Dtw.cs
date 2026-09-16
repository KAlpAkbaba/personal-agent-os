namespace PagentOS.Companion.Audio.Listening.Spotting;

/// <summary>
/// Dynamic time warping over feature frames with a Sakoe-Chiba band. Distances are
/// normalised by path length (n + m) so a long and a short phrase are comparable, and every
/// result is a plain double - the spotter's thresholds are measured numbers, not guesses.
/// </summary>
public static class Dtw
{
    /// <summary>The band half-width as a fraction of the longer sequence.</summary>
    public const double BandFraction = 0.4;

    /// <summary>Whole-sequence distance: template against a complete utterance.</summary>
    public static double Distance(IReadOnlyList<float[]> a, IReadOnlyList<float[]> b)
    {
        int n = a.Count, m = b.Count;
        if (n == 0 || m == 0)
        {
            return double.PositiveInfinity;
        }

        // Lengths more than 2.5x apart are not the same phrase said at a different speed.
        if (n > m * 2.5 || m > n * 2.5)
        {
            return double.PositiveInfinity;
        }

        var band = Math.Max((int)Math.Ceiling(Math.Max(n, m) * BandFraction), Math.Abs(n - m) + 1);
        var cost = Fill(a, b, n, m, i =>
        {
            var centre = n == 1 ? 0 : (int)Math.Round((double)i * (m - 1) / (n - 1));
            return (Math.Max(0, centre - band), Math.Min(m - 1, centre + band));
        });
        return cost[n - 1][m - 1] / (n + m);
    }

    /// <summary>
    /// Open-end match: the whole <paramref name="template"/> against a PREFIX of
    /// <paramref name="input"/> ending anywhere between 0.6x and 1.6x the template length.
    /// Returns the best normalised distance and the input frame index where the phrase ended
    /// (so what the owner said AFTER the wake word can be kept and the wake word dropped).
    /// </summary>
    public static (double Distance, int EndFrame) PrefixDistance(IReadOnlyList<float[]> template, IReadOnlyList<float[]> input)
    {
        int n = template.Count, m = input.Count;
        var minEnd = (int)Math.Floor(n * 0.6);
        if (n == 0 || m < Math.Max(1, minEnd))
        {
            return (double.PositiveInfinity, -1);
        }

        var maxEnd = Math.Min(m, (int)Math.Ceiling(n * 1.6));

        // A parallelogram rather than a diagonal band: the end is free, so row i may sit
        // anywhere a 0.5x..1.7x local speed puts it.
        var cost = Fill(template, input, n, maxEnd, i =>
            (Math.Max(0, (int)(i * 0.5) - 2), Math.Min(maxEnd - 1, (int)Math.Ceiling(i * 1.7) + 2)));
        var best = double.PositiveInfinity;
        var bestEnd = -1;
        for (var j = Math.Max(0, minEnd - 1); j < maxEnd; j++)
        {
            var normalised = cost[n - 1][j] / (n + j + 1);
            if (normalised < best)
            {
                best = normalised;
                bestEnd = j;
            }
        }

        return (best, bestEnd);
    }

    public static double FrameDistance(float[] x, float[] y)
    {
        double sum = 0;
        for (var d = 0; d < x.Length; d++)
        {
            var diff = x[d] - y[d];
            sum += diff * diff;
        }

        return Math.Sqrt(sum);
    }

    private static double[][] Fill(IReadOnlyList<float[]> a, IReadOnlyList<float[]> b, int n, int m, Func<int, (int From, int To)> range)
    {
        var cost = new double[n][];
        for (var i = 0; i < n; i++)
        {
            var row = new double[m];
            Array.Fill(row, double.PositiveInfinity);
            cost[i] = row;
            var (from, to) = range(i);
            for (var j = from; j <= to; j++)
            {
                var local = FrameDistance(a[i], b[j]);
                double previous;
                if (i == 0 && j == 0)
                {
                    previous = 0;
                }
                else
                {
                    previous = double.PositiveInfinity;
                    if (i > 0)
                    {
                        previous = Math.Min(previous, cost[i - 1][j]);
                    }

                    if (j > 0)
                    {
                        previous = Math.Min(previous, row[j - 1]);
                    }

                    if (i > 0 && j > 0)
                    {
                        previous = Math.Min(previous, cost[i - 1][j - 1]);
                    }
                }

                row[j] = local + previous;
            }
        }

        return cost;
    }
}
