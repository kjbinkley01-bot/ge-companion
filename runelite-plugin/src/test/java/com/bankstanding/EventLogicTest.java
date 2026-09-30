package com.bankstanding;

import static org.junit.Assert.assertArrayEquals;
import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import com.google.gson.Gson;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.List;
import java.util.Map;
import net.runelite.api.Item;
import org.junit.Rule;
import org.junit.Test;
import org.junit.rules.TemporaryFolder;

public class EventLogicTest
{
	@Rule
	public TemporaryFolder tmp = new TemporaryFolder();

	@Test
	public void flattenMergesStacksAndDropsEmptySlots()
	{
		Item[] items = {new Item(995, 1000), new Item(-1, 0), new Item(561, 5), new Item(995, 500), null,
			new Item(4152, 2), new Item(4151, 1)};
		// Pretend 4152 is the noted form of 4151 (Abyssal whip).
		int[] flat = BankstandingPlugin.flatten(items, id -> id == 4152 ? 4151 : id);
		assertArrayEquals(new int[]{995, 1500, 561, 5, 4151, 3}, flat);
	}

	@Test
	public void changeFilterOnlyPassesNewValues()
	{
		ChangeFilter f = new ChangeFilter();
		assertTrue(f.changed("a", new int[]{1, 2}));
		assertFalse(f.changed("a", new int[]{1, 2}));
		assertTrue(f.changed("a", new int[]{1, 3}));
		assertTrue(f.changed("b", "x"));
		assertFalse(f.changed("b", "x"));
		f.clear();
		assertTrue(f.changed("b", "x"));
	}

	@Test
	public void writerAppendsOneJsonLinePerEventToAMonthlyFile() throws Exception
	{
		File dir = tmp.newFolder("out");
		EventWriter w = new EventWriter(new Gson(), dir);
		long t = 1790000000000L; // 2026-09
		Map<String, Object> e = EventWriter.event("offer", "rsprofile--1", "Zezima", t);
		e.put("slot", 3);
		w.write(e);
		w.write(EventWriter.event("logout", "rsprofile--1", "Zezima", t + 1));
		File f = new File(dir, "events-2026-09.jsonl");
		List<String> lines = Files.readAllLines(f.toPath(), StandardCharsets.UTF_8);
		assertEquals(2, lines.size());
		Map<?, ?> first = new Gson().fromJson(lines.get(0), Map.class);
		assertEquals("offer", first.get("type"));
		assertEquals(3.0, first.get("slot"));
		assertEquals("rsprofile--1", first.get("acct"));
	}

	@Test
	public void everyTrackedContainerHasAName()
	{
		assertEquals("bank", BankstandingPlugin.CONTAINERS.get(95));
		assertEquals("inventory", BankstandingPlugin.CONTAINERS.get(93));
		assertEquals("equipment", BankstandingPlugin.CONTAINERS.get(94));
		assertEquals(14, BankstandingPlugin.CONTAINERS.size());
	}
}
