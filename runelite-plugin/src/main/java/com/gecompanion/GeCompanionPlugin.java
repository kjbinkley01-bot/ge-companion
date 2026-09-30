package com.gecompanion;

import com.google.gson.Gson;
import com.google.inject.Provides;
import java.io.File;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ScheduledExecutorService;
import javax.inject.Inject;
import lombok.extern.slf4j.Slf4j;
import net.runelite.api.Client;
import net.runelite.api.EnumComposition;
import net.runelite.api.EnumID;
import net.runelite.api.GameState;
import net.runelite.api.GrandExchangeOffer;
import net.runelite.api.Item;
import net.runelite.api.ItemContainer;
import net.runelite.api.Player;
import net.runelite.api.Skill;
import net.runelite.api.events.GameStateChanged;
import net.runelite.api.events.GameTick;
import net.runelite.api.events.GrandExchangeOfferChanged;
import net.runelite.api.events.ItemContainerChanged;
import net.runelite.api.events.StatChanged;
import net.runelite.api.events.VarbitChanged;
import net.runelite.api.gameval.InventoryID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.RuneLite;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.game.ItemManager;
import net.runelite.client.game.ItemStack;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.plugins.loottracker.LootReceived;

/**
 * GE Companion: records account events for the local dashboard.
 *
 * Listen only. This plugin never clicks, moves the mouse, types, or changes anything in
 * the game. It subscribes to events RuneLite already provides and appends them as JSON
 * lines to a file on this computer, which the GE Companion app reads.
 */
@Slf4j
@PluginDescriptor(
	name = "GE Companion",
	description = "Records GE offers, holdings, loot and XP to a local file for the GE Companion dashboard",
	tags = {"grand exchange", "flipping", "net worth", "portfolio", "wealth"}
)
public class GeCompanionPlugin extends Plugin
{
	/** Containers that hold things you own, by the name the dashboard uses. */
	static final Map<Integer, String> CONTAINERS = new HashMap<>();

	static
	{
		CONTAINERS.put(InventoryID.INV, "inventory");
		CONTAINERS.put(InventoryID.WORN, "equipment");
		CONTAINERS.put(InventoryID.BANK, "bank");
		CONTAINERS.put(InventoryID.LOOTING_BAG, "looting_bag");
		CONTAINERS.put(InventoryID.SEED_VAULT, "seed_vault");
		CONTAINERS.put(InventoryID.DEATH_PERMANENT, "death_storage");
		// Collection boxes of the eight GE slots (items or coins waiting to be collected).
		CONTAINERS.put(InventoryID.GE_OFFER_0, "ge_collect_0");
		CONTAINERS.put(InventoryID.GE_OFFER_1, "ge_collect_1");
		CONTAINERS.put(InventoryID.GE_OFFER_2, "ge_collect_2");
		CONTAINERS.put(InventoryID.GE_OFFER_3, "ge_collect_3");
		CONTAINERS.put(InventoryID.GE_OFFER_4, "ge_collect_4");
		CONTAINERS.put(InventoryID.GE_OFFER_5, "ge_collect_5");
		CONTAINERS.put(InventoryID.GE_OFFER_6, "ge_collect_6");
		CONTAINERS.put(InventoryID.GE_OFFER_7, "ge_collect_7");
	}

	static final int[][] RUNE_POUCH = {
		{VarbitID.RUNE_POUCH_TYPE_1, VarbitID.RUNE_POUCH_QUANTITY_1},
		{VarbitID.RUNE_POUCH_TYPE_2, VarbitID.RUNE_POUCH_QUANTITY_2},
		{VarbitID.RUNE_POUCH_TYPE_3, VarbitID.RUNE_POUCH_QUANTITY_3},
		{VarbitID.RUNE_POUCH_TYPE_4, VarbitID.RUNE_POUCH_QUANTITY_4},
		{VarbitID.RUNE_POUCH_TYPE_5, VarbitID.RUNE_POUCH_QUANTITY_5},
		{VarbitID.RUNE_POUCH_TYPE_6, VarbitID.RUNE_POUCH_QUANTITY_6},
	};

	private static final long XP_INTERVAL_MS = 60_000;

	@Inject
	private Client client;

	@Inject
	private GeCompanionConfig config;

	@Inject
	private ConfigManager configManager;

	@Inject
	private Gson gson;

	@Inject
	private ScheduledExecutorService executor;

	@Inject
	private ItemManager itemManager;

	private EventWriter writer;
	private final ChangeFilter filter = new ChangeFilter();
	private final List<Map<String, Object>> pending = new ArrayList<>();
	private final Map<Skill, Long> xpWritten = new HashMap<>();
	private final Map<Skill, int[]> xpLatest = new HashMap<>();
	private String account;
	private String name;
	private boolean runePouchDirty;

	@Override
	protected void startUp()
	{
		String custom = config.outputFolder().trim();
		File folder = custom.isEmpty() ? new File(RuneLite.RUNELITE_DIR, "ge-companion") : new File(custom);
		writer = new EventWriter(gson, folder);
		log.debug("GE Companion writing to {}", folder);
		if (client.getGameState() == GameState.LOGGED_IN)
		{
			runePouchDirty = true;
		}
	}

	@Override
	protected void shutDown()
	{
		flushXp(true);
		account = null;
		name = null;
		pending.clear();
		filter.clear();
	}

	@Provides
	GeCompanionConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(GeCompanionConfig.class);
	}

	// Account --------------------------------------------------------------------------

	@Subscribe
	public void onGameStateChanged(GameStateChanged event)
	{
		GameState state = event.getGameState();
		if (state == GameState.LOGIN_SCREEN || state == GameState.HOPPING)
		{
			flushXp(true);
			if (state == GameState.LOGIN_SCREEN && account != null)
			{
				emit(EventWriter.event("logout", account, name, System.currentTimeMillis()));
			}
			if (state == GameState.LOGIN_SCREEN)
			{
				account = null;
				name = null;
				filter.clear();
			}
		}
	}

	@Subscribe
	public void onGameTick(GameTick tick)
	{
		if (account == null)
		{
			Player me = client.getLocalPlayer();
			String key = configManager.getRSProfileKey();
			if (me != null && me.getName() != null && key != null)
			{
				account = key;
				name = me.getName();
				Map<String, Object> e = EventWriter.event("login", account, name, System.currentTimeMillis());
				e.put("world", client.getWorld());
				e.put("worldTypes", client.getWorldType().toString());
				emit(e);
				// Events that arrived before we knew whose account this is.
				for (Map<String, Object> p : pending)
				{
					p.put("acct", account);
					p.put("name", name);
					emit(p);
				}
				pending.clear();
				runePouchDirty = true;
			}
		}
		if (runePouchDirty && account != null && config.recordContainers())
		{
			runePouchDirty = false;
			writeRunePouch();
		}
		flushXp(false);
	}

	// Grand Exchange -------------------------------------------------------------------

	@Subscribe
	public void onGrandExchangeOfferChanged(GrandExchangeOfferChanged event)
	{
		if (!config.recordOffers())
		{
			return;
		}
		GrandExchangeOffer o = event.getOffer();
		Map<String, Object> e = EventWriter.event("offer", account, name, System.currentTimeMillis());
		e.put("slot", event.getSlot());
		e.put("state", o.getState().name());
		e.put("item", o.getItemId());
		e.put("price", o.getPrice());
		e.put("total", o.getTotalQuantity());
		e.put("done", o.getQuantitySold());
		e.put("spent", o.getSpent());
		String key = "offer:" + event.getSlot();
		String value = o.getState() + "|" + o.getItemId() + "|" + o.getPrice() + "|" + o.getTotalQuantity()
			+ "|" + o.getQuantitySold() + "|" + o.getSpent();
		if (account == null || filter.changed(account + key, value))
		{
			emitOrQueue(e);
		}
	}

	// Holdings ---------------------------------------------------------------------------

	@Subscribe
	public void onItemContainerChanged(ItemContainerChanged event)
	{
		String kind = CONTAINERS.get(event.getContainerId());
		if (kind == null || !config.recordContainers())
		{
			return;
		}
		ItemContainer c = event.getItemContainer();
		int[] flat = flatten(c == null ? new Item[0] : c.getItems(), itemManager::canonicalize);
		if (account != null && !filter.changed(account + "c:" + kind, flat))
		{
			return;
		}
		Map<String, Object> e = EventWriter.event("container", account, name, System.currentTimeMillis());
		e.put("container", kind);
		e.put("items", pairs(flat));
		emitOrQueue(e);
	}

	@Subscribe
	public void onVarbitChanged(VarbitChanged event)
	{
		int id = event.getVarbitId();
		for (int[] slot : RUNE_POUCH)
		{
			if (slot[0] == id || slot[1] == id)
			{
				runePouchDirty = true;
				return;
			}
		}
	}

	private void writeRunePouch()
	{
		EnumComposition runes = client.getEnum(EnumID.RUNEPOUCH_RUNE);
		List<int[]> items = new ArrayList<>();
		for (int[] slot : RUNE_POUCH)
		{
			int type = client.getVarbitValue(slot[0]);
			int qty = client.getVarbitValue(slot[1]);
			if (type > 0 && qty > 0 && runes != null)
			{
				items.add(new int[]{runes.getIntValue(type), qty});
			}
		}
		int[] flat = new int[items.size() * 2];
		for (int i = 0; i < items.size(); i++)
		{
			flat[i * 2] = items.get(i)[0];
			flat[i * 2 + 1] = items.get(i)[1];
		}
		if (!filter.changed(account + "c:rune_pouch", flat))
		{
			return;
		}
		Map<String, Object> e = EventWriter.event("container", account, name, System.currentTimeMillis());
		e.put("container", "rune_pouch");
		e.put("items", pairs(flat));
		emit(e);
	}

	/**
	 * [id, qty, id, qty, ...] with empty slots and placeholders removed, noted items turned
	 * into their normal item id (so they can be priced), and stacks of one item merged.
	 */
	static int[] flatten(Item[] items, java.util.function.IntUnaryOperator canonical)
	{
		Map<Integer, Long> merged = new java.util.LinkedHashMap<>();
		for (Item it : items)
		{
			if (it == null || it.getId() <= 0 || it.getQuantity() <= 0)
			{
				continue;
			}
			merged.merge(canonical.applyAsInt(it.getId()), (long) it.getQuantity(), Long::sum);
		}
		int[] out = new int[merged.size() * 2];
		int i = 0;
		for (Map.Entry<Integer, Long> m : merged.entrySet())
		{
			out[i++] = m.getKey();
			out[i++] = (int) Math.min(Integer.MAX_VALUE, m.getValue());
		}
		return out;
	}

	static List<int[]> pairs(int[] flat)
	{
		List<int[]> out = new ArrayList<>();
		for (int i = 0; i + 1 < flat.length; i += 2)
		{
			out.add(new int[]{flat[i], flat[i + 1]});
		}
		return out;
	}

	// Loot and XP ------------------------------------------------------------------------

	@Subscribe
	public void onLootReceived(LootReceived event)
	{
		if (!config.recordLoot() || account == null)
		{
			return;
		}
		Map<String, Object> e = EventWriter.event("loot", account, name, System.currentTimeMillis());
		e.put("source", event.getName());
		e.put("kind", event.getType() == null ? null : event.getType().name());
		e.put("amount", event.getAmount());
		List<int[]> items = new ArrayList<>();
		for (ItemStack s : event.getItems())
		{
			items.add(new int[]{itemManager.canonicalize(s.getId()), s.getQuantity()});
		}
		e.put("items", items);
		emit(e);
	}

	@Subscribe
	public void onStatChanged(StatChanged event)
	{
		if (!config.recordXp() || event.getXp() <= 0)
		{
			return;
		}
		xpLatest.put(event.getSkill(), new int[]{event.getXp(), event.getLevel()});
	}

	private void flushXp(boolean force)
	{
		if (account == null || xpLatest.isEmpty())
		{
			return;
		}
		long now = System.currentTimeMillis();
		List<Skill> done = new ArrayList<>();
		for (Map.Entry<Skill, int[]> x : xpLatest.entrySet())
		{
			Long last = xpWritten.get(x.getKey());
			if (!force && last != null && now - last < XP_INTERVAL_MS)
			{
				continue;
			}
			if (filter.changed(account + "xp:" + x.getKey().name(), x.getValue()[0]))
			{
				Map<String, Object> e = EventWriter.event("xp", account, name, now);
				e.put("skill", x.getKey().getName());
				e.put("xp", x.getValue()[0]);
				e.put("level", x.getValue()[1]);
				emit(e);
			}
			xpWritten.put(x.getKey(), now);
			done.add(x.getKey());
		}
		done.forEach(xpLatest::remove);
	}

	// Output -----------------------------------------------------------------------------

	private void emitOrQueue(Map<String, Object> e)
	{
		if (account == null)
		{
			if (pending.size() < 500)
			{
				pending.add(e);
			}
			return;
		}
		emit(e);
	}

	private void emit(Map<String, Object> e)
	{
		EventWriter w = writer;
		if (w != null)
		{
			executor.execute(() -> w.write(e));
		}
	}
}
