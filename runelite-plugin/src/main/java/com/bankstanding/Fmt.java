package com.bankstanding;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.text.NumberFormat;
import java.util.Locale;

/** Number formatting and safe JSON reads for the panel. */
final class Fmt
{
	private Fmt()
	{
	}

	static String gp(Double v)
	{
		return v == null ? "-" : NumberFormat.getIntegerInstance(Locale.US).format(Math.round(v));
	}

	/** Short gp: 1.76b, 209.7m, 845k, 9,902. */
	static String shortGp(Double v)
	{
		if (v == null)
		{
			return "-";
		}
		double a = Math.abs(v);
		String s = v < 0 ? "-" : "";
		if (a >= 1e9)
		{
			return s + String.format(Locale.US, "%.2fb", a / 1e9);
		}
		if (a >= 1e7)
		{
			return s + String.format(Locale.US, "%.1fm", a / 1e6);
		}
		if (a >= 1e6)
		{
			return s + String.format(Locale.US, "%.2fm", a / 1e6);
		}
		if (a >= 1e5)
		{
			return s + String.format(Locale.US, "%.0fk", a / 1e3);
		}
		return s + NumberFormat.getIntegerInstance(Locale.US).format(Math.round(a));
	}

	/** Unsigned percentage with the given decimals; the sign is shown by a triangle. */
	static String pct(Double v, int decimals)
	{
		return v == null ? "-" : String.format(Locale.US, "%." + decimals + "f%%", Math.abs(v) * 100);
	}

	/** "12 min", "3.4h", "2 days". */
	static String duration(double seconds)
	{
		if (seconds < 90)
		{
			return Math.max(0, Math.round(seconds)) + "s";
		}
		if (seconds < 5400)
		{
			return Math.round(seconds / 60) + " min";
		}
		if (seconds < 48 * 3600)
		{
			return String.format(Locale.US, "%.1fh", seconds / 3600);
		}
		return Math.round(seconds / 86400) + " days";
	}

	static String hours(Double h)
	{
		if (h == null)
		{
			return "-";
		}
		return h < 1 ? Math.max(1, Math.round(h * 60)) + " min" : String.format(Locale.US, h < 10 ? "%.1fh" : "%.0fh", h);
	}

	static Double num(JsonObject o, String k)
	{
		if (o == null)
		{
			return null;
		}
		JsonElement e = o.get(k);
		return e == null || e.isJsonNull() || !e.isJsonPrimitive() ? null : e.getAsDouble();
	}

	static String str(JsonObject o, String k)
	{
		if (o == null)
		{
			return null;
		}
		JsonElement e = o.get(k);
		return e == null || e.isJsonNull() ? null : e.getAsString();
	}

	static boolean bool(JsonObject o, String k)
	{
		JsonElement e = o == null ? null : o.get(k);
		return e != null && e.isJsonPrimitive() && e.getAsBoolean();
	}

	static JsonObject obj(JsonObject o, String k)
	{
		JsonElement e = o == null ? null : o.get(k);
		return e != null && e.isJsonObject() ? e.getAsJsonObject() : null;
	}

	static JsonArray arr(JsonObject o, String k)
	{
		JsonElement e = o == null ? null : o.get(k);
		return e != null && e.isJsonArray() ? e.getAsJsonArray() : new JsonArray();
	}

	static String esc(String s)
	{
		return s == null ? "" : s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
	}
}
