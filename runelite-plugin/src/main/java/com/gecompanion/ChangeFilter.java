package com.gecompanion;

import java.util.Arrays;
import java.util.HashMap;
import java.util.Map;

/**
 * Remembers the last value written per key so unchanged state is not written again
 * (the game re-sends containers and offers often, for example on every login).
 */
public class ChangeFilter
{
	private final Map<String, Object> last = new HashMap<>();

	/** True when the value differs from the last one recorded for this key (and records it). */
	public boolean changed(String key, Object value)
	{
		Object prev = last.get(key);
		boolean same = prev instanceof int[] && value instanceof int[]
			? Arrays.equals((int[]) prev, (int[]) value)
			: prev != null && prev.equals(value);
		if (same)
		{
			return false;
		}
		last.put(key, value instanceof int[] ? ((int[]) value).clone() : value);
		return true;
	}

	public void clear()
	{
		last.clear();
	}
}
