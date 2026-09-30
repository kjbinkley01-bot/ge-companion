package com.bankstanding;

import com.google.gson.Gson;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.LinkedHashMap;
import java.util.Map;
import lombok.extern.slf4j.Slf4j;

/**
 * Appends one JSON object per line to a monthly file (events-YYYY-MM.jsonl).
 * The dashboard reads these files; nothing is sent over the network.
 */
@Slf4j
public class EventWriter
{
	static final int FORMAT_VERSION = 1;
	private static final DateTimeFormatter MONTH = DateTimeFormatter.ofPattern("yyyy-MM").withZone(ZoneOffset.UTC);

	private final Gson gson;
	private final File folder;

	public EventWriter(Gson gson, File folder)
	{
		this.gson = gson;
		this.folder = folder;
	}

	public File getFolder()
	{
		return folder;
	}

	/** Builds the common envelope for an event. */
	public static Map<String, Object> event(String type, String account, String name, long timeMillis)
	{
		Map<String, Object> e = new LinkedHashMap<>();
		e.put("v", FORMAT_VERSION);
		e.put("t", timeMillis);
		e.put("type", type);
		e.put("acct", account);
		e.put("name", name);
		return e;
	}

	public String toLine(Map<String, Object> event)
	{
		return gson.toJson(event) + "\n";
	}

	public File fileFor(long timeMillis)
	{
		return new File(folder, "events-" + MONTH.format(Instant.ofEpochMilli(timeMillis)) + ".jsonl");
	}

	/** Appends the event. Call off the client thread. */
	public void write(Map<String, Object> event)
	{
		try
		{
			if (!folder.exists() && !folder.mkdirs())
			{
				log.warn("Bankstanding: could not create {}", folder);
				return;
			}
			long t = ((Number) event.get("t")).longValue();
			Files.write(fileFor(t).toPath(), toLine(event).getBytes(StandardCharsets.UTF_8),
				StandardOpenOption.CREATE, StandardOpenOption.APPEND);
		}
		catch (IOException ex)
		{
			log.warn("Bankstanding: could not write event", ex);
		}
	}
}
