package com.bankstanding;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import net.runelite.api.Client;
import net.runelite.api.WidgetNode;
import net.runelite.api.gameval.DBTableID;
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.api.widgets.WidgetModalMode;

/**
 * Reads account state the game already shows you: farming patch values, achievement diary
 * completion and the current Slayer task. Read only; nothing here sends input or changes the game.
 */
final class AccountReader
{
	/** The farming transmit varbits, in the order A to N. Their meaning depends on the region. */
	static final int[] FARM_VARBITS = {
		VarbitID.FARMING_TRANSMIT_A, VarbitID.FARMING_TRANSMIT_B, VarbitID.FARMING_TRANSMIT_C,
		VarbitID.FARMING_TRANSMIT_D, VarbitID.FARMING_TRANSMIT_E, VarbitID.FARMING_TRANSMIT_F,
		VarbitID.FARMING_TRANSMIT_G, VarbitID.FARMING_TRANSMIT_H, VarbitID.FARMING_TRANSMIT_I,
		VarbitID.FARMING_TRANSMIT_J, VarbitID.FARMING_TRANSMIT_K, VarbitID.FARMING_TRANSMIT_L,
		VarbitID.FARMING_TRANSMIT_M, VarbitID.FARMING_TRANSMIT_N,
	};

	/** Regions with farming patches (from RuneLite's Time Tracking farming world). */
	static final java.util.Set<Integer> FARM_REGIONS = new java.util.HashSet<>(java.util.Arrays.asList(
		4665, 4666, 4667, 4921, 4922, 4923, 5021, 5165, 5166, 5167, 5177, 5178, 5179, 5421,
		5422, 5423, 5424, 5427, 5428, 5677, 5678, 5684, 6191, 6192, 6193, 6446, 6447, 6448,
		6449, 6702, 6711, 6967, 7223, 9265, 9525, 9526, 9770, 9777, 9781, 9782, 10033, 10288,
		10290, 10300, 10546, 10548, 10550, 10551, 11056, 11057, 11058, 11060, 11061, 11062, 11103, 11316,
		11317, 11318, 11321, 11325, 11570, 11573, 11826, 11828, 11829, 12082, 12083, 12084, 12126, 12127,
		12325, 12326, 12327, 12340, 12580, 12581, 12582, 12583, 12594, 12596, 12737, 12738, 12836, 12837,
		12838, 12839, 12850, 12851, 12853, 12854, 12894, 12895, 12993, 12994, 13092, 13093, 13105, 13106,
		13150, 13151, 13194, 13250, 13362, 13622, 13878, 14390, 14391, 14650, 14651, 14652, 14906, 14907,
		14908, 15008, 15148, 15162, 15163, 15164
	));

	/** Achievement diary completion varbits: region, then easy, medium, hard, elite. */
	static final Object[][] DIARIES = {
		{"Ardougne", 4458, 4459, 4460, 4461}, {"Desert", 4483, 4484, 4485, 4486},
		{"Falador", 4462, 4463, 4464, 4465}, {"Fremennik", 4491, 4492, 4493, 4494},
		{"Kandarin", 4475, 4476, 4477, 4478}, {"Karamja", 3578, 3599, 3611, 4566},
		{"Kourend & Kebos", 7925, 7926, 7927, 7928}, {"Lumbridge & Draynor", 4495, 4496, 4497, 4498},
		{"Morytania", 4487, 4488, 4489, 4490}, {"Varrock", 4479, 4480, 4481, 4482},
		{"Western Provinces", 4471, 4472, 4473, 4474}, {"Wilderness", 4466, 4467, 4468, 4469},
	};

	/** Varps holding blocked Slayer task IDs. */
	static final int[] BLOCKED = {VarPlayerID.SLAYER_REWARDS_BLOCKED, VarPlayerID.SLAYER_REWARDS_BLOCKED_2,
		VarPlayerID.SLAYER_REWARDS_BLOCKED_3, VarPlayerID.SLAYER_REWARDS_BLOCKED_4,
		VarPlayerID.SLAYER_REWARDS_BLOCKED_5, VarPlayerID.SLAYER_REWARDS_BLOCKED_6};

	private AccountReader()
	{
	}

	/** The game stops sending farming varbits while a modal interface is open. */
	static boolean modalOpen(Client client)
	{
		for (WidgetNode node : client.getComponentTable())
		{
			if (node.getModalMode() != WidgetModalMode.NON_MODAL)
			{
				return true;
			}
		}
		return false;
	}

	/** Fossil Island, where the four bird house spaces are sent (on the ground floor). */
	static final java.util.Set<Integer> FOSSIL_REGIONS = new java.util.HashSet<>(java.util.Arrays.asList(
		14650, 14651, 14652, 14906, 14907, 14908, 15162, 15163, 15164));

	/** Bird house spaces: Meadow north and south, Valley northeast and southwest. */
	static int[] birdHouses(Client client)
	{
		return new int[]{
			client.getVarpValue(VarPlayerID.BIRDHOUSE_TRANSMIT_A), client.getVarpValue(VarPlayerID.BIRDHOUSE_TRANSMIT_B),
			client.getVarpValue(VarPlayerID.BIRDHOUSE_TRANSMIT_C), client.getVarpValue(VarPlayerID.BIRDHOUSE_TRANSMIT_D),
		};
	}

	static int[] farmValues(Client client)
	{
		int[] out = new int[FARM_VARBITS.length];
		for (int i = 0; i < FARM_VARBITS.length; i++)
		{
			out[i] = client.getVarbitValue(FARM_VARBITS[i]);
		}
		return out;
	}

	/** {region: [easy, medium, hard, elite]} as 0 or 1. */
	static Map<String, int[]> diaries(Client client)
	{
		Map<String, int[]> out = new LinkedHashMap<>();
		for (Object[] d : DIARIES)
		{
			int[] tiers = new int[4];
			for (int i = 0; i < 4; i++)
			{
				tiers[i] = client.getVarbitValue((Integer) d[i + 1]) > 0 ? 1 : 0;
			}
			out.put((String) d[0], tiers);
		}
		return out;
	}

	/** Raw Slayer values: [count, target, boss id, area, original count, master, points, streak, wildy streak]. */
	static int[] slayerValues(Client client)
	{
		return new int[]{
			client.getVarpValue(VarPlayerID.SLAYER_COUNT),
			client.getVarpValue(VarPlayerID.SLAYER_TARGET),
			client.getVarbitValue(VarbitID.SLAYER_TARGET_BOSSID),
			client.getVarpValue(VarPlayerID.SLAYER_AREA),
			client.getVarpValue(VarPlayerID.SLAYER_COUNT_ORIGINAL),
			client.getVarbitValue(VarbitID.SLAYER_MASTER),
			client.getVarbitValue(VarbitID.SLAYER_POINTS),
			client.getVarbitValue(VarbitID.SLAYER_TASKS_COMPLETED),
			client.getVarbitValue(VarbitID.SLAYER_WILDERNESS_TASKS_COMPLETED),
		};
	}

	static int[] blockedIds(Client client)
	{
		int[] out = new int[BLOCKED.length];
		for (int i = 0; i < BLOCKED.length; i++)
		{
			out[i] = client.getVarpValue(BLOCKED[i]);
		}
		return out;
	}

	/** A task's name from the game's own Slayer task table, or null. */
	static String taskName(Client client, int taskId, int bossId)
	{
		if (taskId <= 0)
		{
			return null;
		}
		int row;
		if (taskId == 98) // bosses, as the game's own helper reads it
		{
			List<Integer> boss = client.getDBRowsByValue(DBTableID.SlayerTaskSublist.ID,
				DBTableID.SlayerTaskSublist.COL_TASK_SUBTABLE_ID, 0, bossId);
			if (boss.isEmpty())
			{
				return null;
			}
			row = (Integer) client.getDBTableField(boss.get(0), DBTableID.SlayerTaskSublist.COL_TASK, 0)[0];
		}
		else
		{
			List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerTask.ID, DBTableID.SlayerTask.COL_ID, 0, taskId);
			if (rows.isEmpty())
			{
				return null;
			}
			row = rows.get(0);
		}
		Object[] name = client.getDBTableField(row, DBTableID.SlayerTask.COL_NAME_UPPERCASE, 0);
		return name.length > 0 ? (String) name[0] : null;
	}

	static String areaName(Client client, int areaId)
	{
		if (areaId <= 0)
		{
			return null;
		}
		List<Integer> rows = client.getDBRowsByValue(DBTableID.SlayerArea.ID, DBTableID.SlayerArea.COL_AREA_ID, 0, areaId);
		if (rows.isEmpty())
		{
			return null;
		}
		Object[] name = client.getDBTableField(rows.get(0), DBTableID.SlayerArea.COL_AREA_NAME_IN_HELPER, 0);
		return name.length > 0 ? (String) name[0] : null;
	}

	static List<String> blockedNames(Client client, int[] ids)
	{
		List<String> out = new ArrayList<>();
		for (int id : ids)
		{
			String n = id > 0 ? taskName(client, id, 0) : null;
			if (n != null)
			{
				out.add(n);
			}
		}
		return out;
	}

	/**
	 * Whether a region has been stood in long enough for its farming varbits to be current:
	 * after a teleport the old region's values can linger for a tick.
	 */
	static final class RegionClock
	{
		private int region = -1;
		private int ticks;

		boolean settled(int now)
		{
			if (now != region)
			{
				region = now;
				ticks = 0;
				return false;
			}
			ticks++;
			return ticks >= 2;
		}
	}
}
