using System.Globalization;
using System.Text;

namespace ManagerApp.Services;

internal static class CanonicalUrlNormalizer
{
    private static readonly HashSet<string> TrackingQueryKeys = new(StringComparer.OrdinalIgnoreCase)
    {
        "fbclid",
        "gclid",
        "igshid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
    };

    private static readonly HashSet<string> RejectedQueryKeys = new(StringComparer.OrdinalIgnoreCase)
    {
        "_wpnonce",
        "object_id",
    };

    private const string PathSafeCharacters = "/:@!$&'()*+,;=-._~";

    internal static string? Normalize(string? rawUrl, string? baseUrl = null)
    {
        var trimmed = (rawUrl ?? string.Empty).Trim();
        if (string.IsNullOrWhiteSpace(trimmed))
        {
            return null;
        }

        var resolved = ResolveAbsoluteUri(trimmed, baseUrl);
        if (resolved is null)
        {
            return null;
        }

        var scheme = resolved.Scheme.ToLowerInvariant();
        if (!string.Equals(scheme, "http", StringComparison.Ordinal)
            && !string.Equals(scheme, "https", StringComparison.Ordinal))
        {
            return null;
        }

        var host = resolved.Host.ToLowerInvariant();
        if (string.IsNullOrWhiteSpace(host))
        {
            return null;
        }

        var netloc = resolved.IsDefaultPort ? host : $"{host}:{resolved.Port}";
        var path = NormalizePath(resolved.AbsolutePath);
        if (string.IsNullOrWhiteSpace(path))
        {
            return null;
        }

        var query = NormalizeQuery(resolved.Query);
        if (query is null)
        {
            return null;
        }

        return string.IsNullOrWhiteSpace(query)
            ? $"{scheme}://{netloc}{path}"
            : $"{scheme}://{netloc}{path}?{query}";
    }

    private static Uri? ResolveAbsoluteUri(string rawUrl, string? baseUrl)
    {
        if (!string.IsNullOrWhiteSpace(baseUrl)
            && Uri.TryCreate(baseUrl.Trim(), UriKind.Absolute, out var baseUri)
            && Uri.TryCreate(baseUri, rawUrl, out var joined))
        {
            return joined;
        }

        return Uri.TryCreate(rawUrl, UriKind.Absolute, out var absolute)
            ? absolute
            : null;
    }

    private static string? NormalizePath(string rawPath)
    {
        var original = string.IsNullOrEmpty(rawPath) ? "/" : rawPath;
        var decoded = Uri.UnescapeDataString(original);
        if (ContainsEmbeddedAbsoluteUrlTail(decoded))
        {
            return null;
        }

        var segments = decoded.Split('/', StringSplitOptions.None);
        var normalizedSegments = new List<string>(segments.Length);
        foreach (var segment in segments)
        {
            if (string.IsNullOrEmpty(segment) || string.Equals(segment, ".", StringComparison.Ordinal))
            {
                continue;
            }

            if (string.Equals(segment, "..", StringComparison.Ordinal))
            {
                if (normalizedSegments.Count > 0)
                {
                    normalizedSegments.RemoveAt(normalizedSegments.Count - 1);
                }

                continue;
            }

            normalizedSegments.Add(segment);
        }

        var normalized = "/" + string.Join("/", normalizedSegments);
        if (original.EndsWith("/", StringComparison.Ordinal)
            && !normalized.EndsWith("/", StringComparison.Ordinal))
        {
            normalized += "/";
        }

        if (string.IsNullOrEmpty(normalized) || string.Equals(normalized, "/.", StringComparison.Ordinal))
        {
            normalized = "/";
        }

        return EncodePath(normalized);
    }

    private static string? NormalizeQuery(string rawQuery)
    {
        var queryText = (rawQuery ?? string.Empty).TrimStart('?');
        if (string.IsNullOrWhiteSpace(queryText))
        {
            return string.Empty;
        }

        var filtered = new List<KeyValuePair<string, string>>();
        foreach (var pair in ParseQueryPairs(queryText))
        {
            var lowered = pair.Key.ToLowerInvariant();
            if (lowered.StartsWith("utm_", StringComparison.Ordinal)
                || TrackingQueryKeys.Contains(lowered))
            {
                continue;
            }

            if (ShouldRejectQueryPair(lowered, pair.Value))
            {
                return null;
            }

            filtered.Add(pair);
        }

        filtered.Sort((left, right) =>
        {
            var keyCompare = string.Compare(left.Key, right.Key, StringComparison.Ordinal);
            if (keyCompare != 0)
            {
                return keyCompare;
            }

            return string.Compare(left.Value, right.Value, StringComparison.Ordinal);
        });

        if (filtered.Count == 0)
        {
            return string.Empty;
        }

        return string.Join(
            '&',
            filtered.Select(item => $"{EncodeQueryComponent(item.Key)}={EncodeQueryComponent(item.Value)}"));
    }

    private static bool ShouldRejectQueryPair(string loweredKey, string value)
    {
        if (RejectedQueryKeys.Contains(loweredKey))
        {
            return true;
        }

        if (loweredKey.StartsWith("bbp_", StringComparison.Ordinal))
        {
            return true;
        }

        return string.Equals(loweredKey, "action", StringComparison.Ordinal)
            && value.Trim().StartsWith("bbp_", StringComparison.OrdinalIgnoreCase);
    }

    private static bool ContainsEmbeddedAbsoluteUrlTail(string decodedPath)
    {
        if (string.IsNullOrWhiteSpace(decodedPath))
        {
            return false;
        }

        foreach (var rawSegment in decodedPath.Split('/', StringSplitOptions.None))
        {
            var segment = rawSegment.Trim();
            if (string.IsNullOrWhiteSpace(segment))
            {
                continue;
            }

            var stripped = segment.TrimStart('[', ']', '(', ')');
            if (stripped.StartsWith("http:", StringComparison.OrdinalIgnoreCase)
                || stripped.StartsWith("https:", StringComparison.OrdinalIgnoreCase))
            {
                return true;
            }
        }

        return false;
    }

    private static IEnumerable<KeyValuePair<string, string>> ParseQueryPairs(string query)
    {
        foreach (var segment in query.Split('&', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries))
        {
            var separatorIndex = segment.IndexOf('=');
            string keyRaw;
            string valueRaw;

            if (separatorIndex < 0)
            {
                keyRaw = segment;
                valueRaw = string.Empty;
            }
            else
            {
                keyRaw = segment[..separatorIndex];
                valueRaw = separatorIndex >= segment.Length - 1
                    ? string.Empty
                    : segment[(separatorIndex + 1)..];
            }

            yield return new KeyValuePair<string, string>(
                DecodeQueryComponent(keyRaw),
                DecodeQueryComponent(valueRaw));
        }
    }

    private static string DecodeQueryComponent(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return string.Empty;
        }

        var withSpaces = value.Replace("+", " ", StringComparison.Ordinal);
        return Uri.UnescapeDataString(withSpaces);
    }

    private static string EncodeQueryComponent(string value)
    {
        if (string.IsNullOrEmpty(value))
        {
            return string.Empty;
        }

        return Uri.EscapeDataString(value).Replace("%20", "+", StringComparison.Ordinal);
    }

    private static string EncodePath(string value)
    {
        var bytes = Encoding.UTF8.GetBytes(value);
        var builder = new StringBuilder(bytes.Length);

        foreach (var b in bytes)
        {
            var ch = (char)b;
            if (IsAlphaNumeric(ch) || PathSafeCharacters.IndexOf(ch) >= 0)
            {
                builder.Append(ch);
                continue;
            }

            builder.Append('%');
            builder.Append(b.ToString("X2", CultureInfo.InvariantCulture));
        }

        return builder.ToString();
    }

    private static bool IsAlphaNumeric(char ch)
    {
        return (ch >= 'a' && ch <= 'z')
            || (ch >= 'A' && ch <= 'Z')
            || (ch >= '0' && ch <= '9');
    }
}
