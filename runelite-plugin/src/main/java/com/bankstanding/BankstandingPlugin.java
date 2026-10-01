package com.bankstanding;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.inject.Provides;
import java.io.File;
import java.io.IOException;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;
import javax.swing.JLabel;
import javax.swing.SwingUtilities;
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
import net.runelite.api.gameval.VarPlayerID;
import net.runelite.api.gameval.VarbitID;
import net.runelite.client.Notifier;
import net.runelite.client.RuneLite;
import net.runelite.client.callback.ClientThread;
import net.runelite.client.config.ConfigManager;
import net.runelite.client.eventbus.Subscribe;
import net.runelite.client.game.ItemManager;
import net.runelite.client.game.ItemStack;
import net.runelite.client.plugins.Plugin;
import net.runelite.client.plugins.PluginDescriptor;
import net.runelite.client.plugins.loottracker.LootReceived;
import net.runelite.client.ui.ClientToolbar;
import net.runelite.client.ui.NavigationButton;
import net.runelite.client.ui.overlay.OverlayManager;
import net.runelite.client.util.LinkBrowser;
import net.runelite.http.api.item.ItemPrice;
import okhttp3.MediaType;
import okhttp3.RequestBody;
import okhttp3.Call;
import okhttp3.Callback;
import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.Response;
import okhttp3.ResponseBody;

/**
 * Bankstanding: records account events for the local dashboard.
 *
 * Listen only. This plugin never clicks, moves the mouse, types, or changes anything in
 * the game. It subscribes to events RuneLite already provides and appends them as JSON
 * lines to a file on this computer, which the Bankstanding app reads.
 */
@Slf4j
@PluginDescriptor(
	name = "Bankstanding",
	description = "Records GE offers, holdings, loot and XP to a local file for the Bankstanding dashboard",
	tags = {"grand exchange", "flipping", "net worth", "portfolio", "wealth"}
)
public class BankstandingPlugin extends Plugin
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
	private BankstandingConfig config;

	@Inject
	private ConfigManager configManager;

	@Inject
	private Gson gson;

	@Inject
	private ScheduledExecutorService executor;

	@Inject
	private ItemManager itemManager;

	@Inject
	private ClientToolbar clientToolbar;

	@Inject
	private OkHttpClient http;

	@Inject
	private ClientThread clientThread;

	@Inject
	private Notifier notifier;

	@Inject
	private OverlayManager overlayManager;

	@Inject
	private BankCostOverlay bankOverlay;

	private EventWriter writer;
	private BankstandingPanel panel;
	private NavigationButton navButton;
	private ScheduledFuture<?> poller;
	private int geItem = -1;
	private final ChangeFilter filter = new ChangeFilter();
	private final List<Map<String, Object>> pending = new ArrayList<>();
	private final Map<Skill, Long> xpWritten = new HashMap<>();
	private final Map<Skill, int[]> xpLatest = new HashMap<>();
	private String account;
	private String name;
	private boolean runePouchDirty;
	private volatile int selectedItem = -1;
	private volatile long lastNid;
	private volatile JsonObject lastApp;
	private volatile long lastAppTime;
	private final AtomicBoolean refreshQueued = new AtomicBoolean();
	private static final MediaType JSON = MediaType.parse("application/json; charset=utf-8");

	@Override
	protected void startUp()
	{
		String custom = config.outputFolder().trim();
		File folder = custom.isEmpty() ? new File(RuneLite.RUNELITE_DIR, "bankstanding") : new File(custom);
		writer = new EventWriter(gson, folder);
		log.debug("Bankstanding writing to {}", folder);
		if (client.getGameState() == GameState.LOGGED_IN)
		{
			runePouchDirty = true;
		}
		if (config.showPanel())
		{
			panel = new BankstandingPanel(new Host());
			navButton = NavigationButton.builder().tooltip("Bankstanding").icon(BankstandingPanel.icon())
				.priority(7).panel(panel).build();
			clientToolbar.addNavigation(navButton);
			poller = executor.scheduleWithFixedDelay(this::refreshPanel, 2, 30, TimeUnit.SECONDS);
		}
		overlayManager.add(bankOverlay);
	}

	@Override
	protected void shutDown()
	{
		flushXp(true);
		account = null;
		name = null;
		pending.clear();
		filter.clear();
		if (poller != null)
		{
			poller.cancel(false);
			poller = null;
		}
		if (navButton != null)
		{
			clientToolbar.removeNavigation(navButton);
			navButton = null;
		}
		panel = null;
		overlayManager.remove(bankOverlay);
		bankOverlay.setCosts(null);
	}

	// Side panel (reads the local app; display only) -----------------------------------

	/** Refreshes soon, folding a burst of triggers (several offer updates in one tick) into one. */
	private void requestRefresh()
	{
		if (panel != null && refreshQueued.compareAndSet(false, true))
		{
			executor.schedule(() ->
			{
				refreshQueued.set(false);
				refreshPanel();
			}, 400, TimeUnit.MILLISECONDS);
		}
	}

	/** Reads the GE slots on the client thread, then asks the app for the rest. */
	private void refreshPanel()
	{
		if (panel == null)
		{
			return;
		}
		clientThread.invokeLater(() ->
		{
			PanelState st = new PanelState();
			st.selectedItem = selectedItem;
			st.loggedIn = client.getGameState() == GameState.LOGGED_IN;
			if (st.loggedIn)
			{
				GrandExchangeOffer[] offers = client.getGrandExchangeOffers();
				for (int i = 0; offers != null && i < offers.length; i++)
				{
					GrandExchangeOffer o = offers[i];
					PanelState.Slot sl = new PanelState.Slot();
					sl.slot = i;
					if (o != null && o.getState() != null)
					{
						sl.state = o.getState().name();
						sl.itemId = o.getItemId();
						sl.price = o.getPrice();
						sl.total = o.getTotalQuantity();
						sl.done = o.getQuantitySold();
						sl.spent = o.getSpent();
						if (sl.itemId > 0)
						{
							sl.name = itemManager.getItemComposition(sl.itemId).getName();
							addLocal(st, sl.itemId);
						}
					}
					st.slots.add(sl);
				}
			}
			if (st.selectedItem > 0)
			{
				addLocal(st, st.selectedItem);
			}
			executor.execute(() -> fetchApp(st));
		});
	}

	private void addLocal(PanelState st, int id)
	{
		if (st.local.containsKey(id))
		{
			return;
		}
		PanelState.Local l = new PanelState.Local();
		net.runelite.api.ItemComposition c = itemManager.getItemComposition(id);
		l.name = c.getName();
		l.haPrice = c.getHaPrice();
		l.price = itemManager.getItemPrice(id);
		st.local.put(id, l);
	}

	private HttpUrl appUrl(String path)
	{
		HttpUrl base = HttpUrl.parse(config.appUrl().trim());
		return base == null ? null : base.newBuilder().addPathSegments(path).build();
	}

	private void fetchApp(PanelState st)
	{
		HttpUrl u = appUrl("api/plugin/panel");
		if (u == null)
		{
			finish(st, null);
			return;
		}
		StringBuilder ids = new StringBuilder();
		for (PanelState.Slot sl : st.slots)
		{
			if (sl.itemId > 0)
			{
				ids.append(ids.length() > 0 ? "," : "").append(sl.itemId);
			}
		}
		HttpUrl.Builder b = u.newBuilder().addQueryParameter("slots", ids.toString())
			.addQueryParameter("since", Long.toString(lastNid));
		if (account != null)
		{
			b.addQueryParameter("acct", account);
		}
		if (st.selectedItem > 0)
		{
			b.addQueryParameter("item", Integer.toString(st.selectedItem));
		}
		http.newCall(new Request.Builder().url(b.build()).build()).enqueue(new Callback()
		{
			@Override
			public void onFailure(Call call, IOException e)
			{
				finish(st, null);
			}

			@Override
			public void onResponse(Call call, Response response) throws IOException
			{
				try (ResponseBody body = response.body())
				{
					JsonObject o = response.isSuccessful() && body != null ? gson.fromJson(body.string(), JsonObject.class) : null;
					finish(st, o);
				}
				catch (RuntimeException e)
				{
					finish(st, null);
				}
			}
		});
	}

	private void finish(PanelState st, JsonObject o)
	{
		long now = System.currentTimeMillis();
		if (o != null)
		{
			lastApp = o;
			lastAppTime = now;
			st.app = o;
			st.appTime = now;
			JsonObject h = Fmt.obj(o, "header");
			st.status = Fmt.bool(h, "live") ? PanelState.AppStatus.LIVE : PanelState.AppStatus.STALE;
			Double top = Fmt.num(o, "lastNid");
			if (lastNid > 0 && config.notifyAlerts())
			{
				for (com.google.gson.JsonElement e : Fmt.arr(o, "notifications"))
				{
					notifier.notify("Bankstanding: " + Fmt.str(e.getAsJsonObject(), "message"));
				}
			}
			if (top != null)
			{
				lastNid = Math.max(lastNid, top.longValue());
			}
			bankOverlay.setCosts(config.bankTooltip() ? Fmt.obj(o, "costs") : null);
		}
		else if (lastApp != null && now - lastAppTime < 10 * 60_000)
		{
			// Keep showing the last answer for a while, marked as old.
			st.app = lastApp;
			st.appTime = lastAppTime;
			st.status = PanelState.AppStatus.STALE;
		}
		else
		{
			st.status = PanelState.AppStatus.OFFLINE;
			bankOverlay.setCosts(null);
		}
		BankstandingPanel p = panel;
		if (p != null)
		{
			SwingUtilities.invokeLater(() -> p.update(st));
		}
	}

	private void send(String method, HttpUrl u, Object body)
	{
		if (u == null)
		{
			return;
		}
		Request.Builder rb = new Request.Builder().url(u);
		if ("DELETE".equals(method))
		{
			rb.delete();
		}
		else
		{
			rb.post(RequestBody.create(JSON, gson.toJson(body)));
		}
		http.newCall(rb.build()).enqueue(new Callback()
		{
			@Override
			public void onFailure(Call call, IOException e)
			{
				log.debug("Bankstanding app request failed", e);
			}

			@Override
			public void onResponse(Call call, Response response)
			{
				response.close();
				requestRefresh();
			}
		});
	}

	/** What the panel can ask of the plugin. Nothing here touches the game. */
	private class Host implements PanelHost
	{
		@Override
		public void icon(int itemId, int quantity, JLabel target)
		{
			itemManager.getImage(itemId, Math.max(1, quantity), quantity > 1).addTo(target);
		}

		@Override
		public void selectItem(int itemId)
		{
			selectedItem = itemId;
			requestRefresh();
		}

		@Override
		public void search(String query, Consumer<List<Object[]>> results)
		{
			clientThread.invokeLater(() ->
			{
				List<Object[]> out = new ArrayList<>();
				for (ItemPrice ip : itemManager.search(query))
				{
					out.add(new Object[]{ip.getId(), ip.getName()});
					if (out.size() >= 25)
					{
						break;
					}
				}
				SwingUtilities.invokeLater(() -> results.accept(out));
			});
		}

		@Override
		public void openDashboard(String path)
		{
			LinkBrowser.browse(config.appUrl().trim().replaceAll("/+$", "") + path);
		}

		@Override
		public void setWatched(int itemId, boolean watched)
		{
			if (watched)
			{
				Map<String, Object> b = new HashMap<>();
				b.put("id", itemId);
				send("POST", appUrl("api/watchlist"), b);
			}
			else
			{
				HttpUrl u = appUrl("api/watchlist");
				send("DELETE", u == null ? null : u.newBuilder().addQueryParameter("id", Integer.toString(itemId)).build(), null);
			}
		}

		@Override
		public void addTarget(int itemId, String side, long price, Long quantity)
		{
			Map<String, Object> b = new HashMap<>();
			b.put("item_id", itemId);
			b.put("side", side);
			b.put("price", price);
			b.put("qty", quantity);
			send("POST", appUrl("api/targets"), b);
		}

		@Override
		public void refresh()
		{
			requestRefresh();
		}
	}

	@Provides
	BankstandingConfig provideConfig(ConfigManager configManager)
	{
		return configManager.getConfig(BankstandingConfig.class);
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
				requestRefresh();
			}
		}
		if (runePouchDirty && account != null && config.recordContainers())
		{
			runePouchDirty = false;
			writeRunePouch();
		}
		flushXp(false);
		// The item picked on the GE offer screen: refresh the panel as soon as it changes.
		int item = client.getVarpValue(VarPlayerID.TRADINGPOST_SEARCH);
		if (item != geItem)
		{
			geItem = item;
			if (panel != null && item > 0 && config.followGeItem())
			{
				selectedItem = item;
				BankstandingPanel p = panel;
				SwingUtilities.invokeLater(p::showItemTab);
				requestRefresh();
			}
		}
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
		requestRefresh();
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
