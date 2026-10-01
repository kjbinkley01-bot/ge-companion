package com.bankstanding;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.awt.BasicStroke;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.FlowLayout;
import java.awt.Font;
import java.awt.Graphics2D;
import java.awt.GridLayout;
import java.awt.Rectangle;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import javax.swing.BorderFactory;
import javax.swing.JButton;
import javax.swing.JComboBox;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextField;
import javax.swing.Scrollable;
import javax.swing.SwingConstants;
import net.runelite.client.ui.ColorScheme;
import net.runelite.client.ui.FontManager;
import net.runelite.client.ui.PluginPanel;
import net.runelite.client.ui.components.IconTextField;
import net.runelite.client.ui.components.ProgressBar;
import net.runelite.client.ui.components.materialtabs.MaterialTab;
import net.runelite.client.ui.components.materialtabs.MaterialTabGroup;

/**
 * The Bankstanding side panel: net worth in the header, then four tabs.
 *
 * Offers: your eight GE slots from the game, with the market price and the offer coach's tip
 * on each. Item: any item in detail (follows the GE screen, or search). Ideas: flips your
 * cash can fund now, and your watchlist. Account: what changed today and what needs a look.
 *
 * Display only: it never clicks, types or changes anything in the game. Buttons open the
 * dashboard or save things in the Bankstanding app on this computer.
 */
class BankstandingPanel extends PluginPanel
{
	private final PanelHost host;
	private final JPanel header = new JPanel(new BorderLayout(0, 2));
	private final JPanel offers = Ui.stack(6);
	private final JPanel item = Ui.stack(6);
	private final JPanel ideas = Ui.stack(5);
	private final JPanel account = Ui.stack(5);
	private final MaterialTabGroup tabs;
	private final MaterialTab offersTab;
	private final MaterialTab itemTab;
	private final MaterialTab ideasTab;
	private final MaterialTab accountTab;
	private final IconTextField search = new IconTextField();
	private PanelState state = new PanelState();
	private List<Object[]> results;
	private boolean showWatchlist;

	BankstandingPanel(PanelHost host)
	{
		super(false);
		this.host = host;
		setLayout(new BorderLayout());
		setBackground(Ui.BG);

		header.setBackground(Ui.BG);
		header.setBorder(BorderFactory.createEmptyBorder(8, 10, 6, 10));

		search.setIcon(IconTextField.Icon.SEARCH);
		search.setPreferredSize(new Dimension(Ui.WIDTH, 28));
		search.setBackground(ColorScheme.DARKER_GRAY_COLOR);
		search.setHoverBackgroundColor(ColorScheme.DARKER_GRAY_HOVER_COLOR);
		search.addActionListener(e -> runSearch());
		search.addClearListener(() ->
		{
			results = null;
			renderItem();
		});
		JPanel itemPage = new JPanel(new BorderLayout(0, 6));
		itemPage.setBackground(Ui.BG);
		JPanel searchWrap = new JPanel(new BorderLayout());
		searchWrap.setBackground(Ui.BG);
		searchWrap.setBorder(BorderFactory.createEmptyBorder(6, 10, 0, 10));
		searchWrap.add(search);
		itemPage.add(searchWrap, BorderLayout.NORTH);
		itemPage.add(scroll(item), BorderLayout.CENTER);

		JPanel display = new JPanel(new BorderLayout());
		display.setBackground(Ui.BG);
		tabs = new MaterialTabGroup(display);
		tabs.setLayout(new GridLayout(1, 4, 0, 0));
		tabs.setBackground(Ui.BG);
		tabs.setBorder(BorderFactory.createEmptyBorder(0, 4, 0, 4));
		offersTab = new MaterialTab("Offers", tabs, scroll(offers));
		itemTab = new MaterialTab("Item", tabs, itemPage);
		ideasTab = new MaterialTab("Ideas", tabs, scroll(ideas));
		accountTab = new MaterialTab("Wealth", tabs, scroll(account));
		for (MaterialTab t : new MaterialTab[]{offersTab, itemTab, ideasTab, accountTab})
		{
			t.setHorizontalAlignment(SwingConstants.CENTER);
			t.setFont(FontManager.getRunescapeSmallFont());
			tabs.addTab(t);
		}
		tabs.select(offersTab);

		JPanel north = new JPanel(new BorderLayout());
		north.setBackground(Ui.BG);
		north.add(header, BorderLayout.NORTH);
		north.add(tabs, BorderLayout.SOUTH);
		add(north, BorderLayout.NORTH);
		add(display, BorderLayout.CENTER);
		render();
	}

	/** New data. Call on the Swing thread. */
	void update(PanelState s)
	{
		state = s;
		render();
	}

	/** Shows the Item tab (used when an item is picked on the GE screen or in a list). */
	void showItemTab()
	{
		tabs.select(itemTab);
	}

	void showTab(int i)
	{
		tabs.select(new MaterialTab[]{offersTab, itemTab, ideasTab, accountTab}[i]);
	}

	private void render()
	{
		renderHeader();
		renderOffers();
		renderItem();
		renderIdeas();
		renderAccount();
	}

	private JsonObject app()
	{
		return state.app;
	}

	// Header ---------------------------------------------------------------------------------

	private void renderHeader()
	{
		header.removeAll();
		JsonObject h = Fmt.obj(app(), "header");
		JPanel top = new JPanel(new BorderLayout());
		top.setOpaque(false);
		JLabel status;
		long age = state.appTime > 0 ? (System.currentTimeMillis() - state.appTime) / 1000 : -1;
		if (state.status == PanelState.AppStatus.LIVE)
		{
			status = Ui.small("Live");
			status.setIcon(new Ui.Dot(Ui.UP));
		}
		else if (state.status == PanelState.AppStatus.STALE)
		{
			status = Ui.small(age > 90 ? "Updated " + Fmt.duration(age) + " ago" : "Prices are old");
			status.setIcon(new Ui.Dot(Ui.WARN));
		}
		else
		{
			status = Ui.small("App offline");
			status.setIcon(new Ui.Dot(Ui.DOWN));
		}
		status.setIconTextGap(5);
		status.setToolTipText(state.status == PanelState.AppStatus.OFFLINE
			? "The Bankstanding app is not answering. Prices below come from RuneLite."
			: "Connected to the Bankstanding app on this computer.");
		top.add(status, BorderLayout.WEST);
		JPanel buttons = new JPanel(new FlowLayout(FlowLayout.RIGHT, 2, 0));
		buttons.setOpaque(false);
		JButton refresh = smallButton("Refresh", "Refresh now");
		refresh.addActionListener(e -> host.refresh());
		JButton open = smallButton("Dashboard", "Open the Bankstanding dashboard in your browser");
		open.addActionListener(e -> host.openDashboard("/"));
		buttons.add(refresh);
		buttons.add(open);
		top.add(buttons, BorderLayout.EAST);
		header.add(top, BorderLayout.NORTH);

		JPanel body = Ui.stack(1);
		if (h != null)
		{
			JLabel total = Ui.label(Fmt.shortGp(Fmt.num(h, "total")) + " gp", FontManager.getRunescapeBoldFont().deriveFont(24f), Ui.TEXT);
			total.setToolTipText(Fmt.gp(Fmt.num(h, "total")) + " gp (valued at sell price after tax)");
			body.add(total);
			JPanel line = new JPanel(new BorderLayout());
			line.setOpaque(false);
			Double d1 = Fmt.num(h, "d1");
			JLabel ch = d1 == null ? Ui.small("No change recorded yet")
				: Ui.change(Fmt.shortGp(Math.abs(d1)) + " (" + Fmt.pct(Fmt.num(h, "d1pct"), 2) + ") today", d1, FontManager.getRunescapeSmallFont());
			line.add(ch, BorderLayout.WEST);
			line.add(Ui.small(Fmt.str(h, "acctName")), BorderLayout.EAST);
			body.add(line);
		}
		else
		{
			body.add(Ui.label("Net worth", FontManager.getRunescapeBoldFont().deriveFont(16f), Ui.TEXT));
			body.add(Ui.wrap("Start Bankstanding on this PC to see your net worth, tips and ideas here.", Ui.MUTED, 185));
		}
		header.add(body, BorderLayout.CENTER);
		header.revalidate();
		header.repaint();
	}

	private static JButton smallButton(String text, String tip)
	{
		JButton b = new JButton(text);
		b.setFont(FontManager.getRunescapeSmallFont());
		b.setFocusable(false);
		b.setMargin(new java.awt.Insets(1, 5, 1, 5));
		b.setToolTipText(tip);
		return b;
	}

	// Offers ---------------------------------------------------------------------------------

	private void renderOffers()
	{
		offers.removeAll();
		if (!state.loggedIn)
		{
			offers.add(note("Log in to see your Grand Exchange slots."));
			done(offers);
			return;
		}
		int active = 0, finished = 0;
		List<Integer> empty = new ArrayList<>();
		for (PanelState.Slot s : state.slots)
		{
			if (s.empty())
			{
				empty.add(s.slot + 1);
			}
			else if (s.finished())
			{
				finished++;
			}
			else
			{
				active++;
			}
		}
		offers.add(Ui.small(active + " active" + (finished > 0 ? ", " + finished + " ready to collect" : "")
			+ (empty.isEmpty() ? "" : ", " + empty.size() + " free")));
		JsonObject notes = Fmt.obj(app(), "slotNotes");
		for (PanelState.Slot s : state.slots)
		{
			if (!s.empty())
			{
				offers.add(slotCard(s, Fmt.obj(notes, Integer.toString(s.slot))));
			}
		}
		if (!empty.isEmpty())
		{
			StringBuilder sb = new StringBuilder("Free slots: ");
			for (int i = 0; i < empty.size(); i++)
			{
				sb.append(i > 0 ? ", " : "").append(empty.get(i));
			}
			offers.add(Ui.small(sb.toString()));
		}
		if (state.status == PanelState.AppStatus.OFFLINE)
		{
			offers.add(Ui.wrap("Market prices here come from RuneLite. With the Bankstanding app running you also get fill times and a suggested price for offers that stall.", Ui.MUTED, 190));
		}
		done(offers);
	}

	private JPanel slotCard(PanelState.Slot s, JsonObject coach)
	{
		JPanel card = Ui.card(null);
		JLabel icon = new JLabel();
		icon.setPreferredSize(new Dimension(36, 32));
		icon.setVerticalAlignment(SwingConstants.TOP);
		host.icon(s.itemId, s.finished() ? Math.max(1, s.done) : 1, icon);
		card.add(icon, BorderLayout.WEST);

		JPanel body = Ui.stack(2);
		JPanel title = new JPanel(new BorderLayout(4, 0));
		title.setOpaque(false);
		title.add(Ui.bold(s.name), BorderLayout.CENTER);
		title.add(Ui.small("Slot " + (s.slot + 1)), BorderLayout.EAST);
		body.add(title);

		JPanel line = new JPanel(new BorderLayout(4, 0));
		line.setOpaque(false);
		JLabel side = Ui.label(s.buy() ? "Buy" : "Sell", FontManager.getRunescapeSmallFont(), s.buy() ? Ui.UP : Ui.DOWN);
		line.add(side, BorderLayout.WEST);
		line.add(Ui.label(Fmt.gp((double) s.price) + " each", FontManager.getRunescapeSmallFont(), Ui.TEXT), BorderLayout.CENTER);
		body.add(line);

		ProgressBar bar = new ProgressBar();
		bar.setMaximumValue(Math.max(1, s.total));
		bar.setValue(s.done);
		bar.setBackground(ColorScheme.DARK_GRAY_HOVER_COLOR);
		bar.setForeground(s.state.startsWith("CANCELLED") ? ColorScheme.PROGRESS_ERROR_COLOR
			: s.finished() ? ColorScheme.PROGRESS_COMPLETE_COLOR : ColorScheme.PROGRESS_INPROGRESS_COLOR);
		bar.setPreferredSize(new Dimension(150, 5));
		body.add(bar);
		JPanel filled = new JPanel(new BorderLayout());
		filled.setOpaque(false);
		filled.add(Ui.small(Fmt.gp((double) s.done) + " of " + Fmt.gp((double) s.total)), BorderLayout.WEST);
		filled.add(Ui.small(Math.round(100.0 * s.done / Math.max(1, s.total)) + "%"), BorderLayout.EAST);
		body.add(filled);

		if (s.finished())
		{
			String what = s.state.startsWith("CANCELLED") ? "Cancelled at " + Fmt.gp((double) s.done)
				: (s.buy() ? "Bought " : "Sold ") + Fmt.gp((double) s.done) + " for " + Fmt.shortGp((double) s.spent);
			body.add(Ui.wrap(what + ", ready to collect", s.state.startsWith("CANCELLED") ? Ui.MUTED : Ui.UP, 140));
		}
		else
		{
			Double market = marketPrice(s.itemId, s.buy());
			if (market != null && market > 0)
			{
				double gap = s.buy() ? market / s.price - 1 : s.price / market - 1;
				JLabel m;
				if (gap > 0.003)
				{
					m = Ui.label("Market " + Fmt.gp(market) + ", " + Fmt.pct(gap, 1) + (s.buy() ? " above you" : " under you"),
						FontManager.getRunescapeSmallFont(), Ui.WARN);
				}
				else
				{
					m = Ui.label("Market " + Fmt.gp(market) + ", priced to fill", FontManager.getRunescapeSmallFont(), Ui.MUTED);
				}
				body.add(m);
			}
			if (coach != null)
			{
				Double sug = Fmt.num(coach, "suggest");
				Double eta = Fmt.num(coach, "eta");
				String tip = sug != null ? "Try " + Fmt.gp(sug) + (eta != null ? ", fills in about " + Fmt.hours(eta) : "")
					: Fmt.str(coach, "title");
				JLabel t = Ui.wrap(tip, Ui.WARN, 140);
				t.setToolTipText("<html><div style='width:260px'>" + Fmt.esc(Fmt.str(coach, "detail")) + "</div></html>");
				body.add(t);
			}
		}
		card.add(body, BorderLayout.CENTER);
		Ui.clickable(card, () ->
		{
			host.selectItem(s.itemId);
			showItemTab();
		});
		card.setToolTipText("Show " + s.name + " on the Item tab");
		return card;
	}

	/** Market price a slot is competing with: instant sells for a buy, instant buys for a sell. */
	private Double marketPrice(int id, boolean buy)
	{
		JsonObject p = Fmt.obj(Fmt.obj(app(), "prices"), Integer.toString(id));
		Double v = p == null ? null : Fmt.num(p, buy ? "low" : "high");
		if (v == null)
		{
			PanelState.Local l = state.local.get(id);
			v = l != null && l.price > 0 ? (double) l.price : null;
		}
		return v;
	}

	// Item -----------------------------------------------------------------------------------

	private void runSearch()
	{
		String q = search.getText().trim();
		if (q.length() < 2)
		{
			return;
		}
		host.search(q, r ->
		{
			results = r;
			renderItem();
		});
	}

	private void renderItem()
	{
		item.removeAll();
		if (results != null)
		{
			item.add(Ui.small(results.isEmpty() ? "No tradeable items match." : "Pick an item:"));
			for (Object[] r : results)
			{
				int id = (Integer) r[0];
				JPanel row = Ui.card(BorderFactory.createEmptyBorder(4, 6, 4, 6));
				JLabel ic = new JLabel();
				ic.setPreferredSize(new Dimension(32, 28));
				host.icon(id, 1, ic);
				row.add(ic, BorderLayout.WEST);
				row.add(Ui.plain((String) r[1]), BorderLayout.CENTER);
				Ui.clickable(row, () ->
				{
					results = null;
					search.setText("");
					host.selectItem(id);
				});
				item.add(row);
			}
			done(item);
			return;
		}
		int id = state.selectedItem;
		if (id <= 0)
		{
			item.add(note("Pick an item on the Grand Exchange offer screen, or search above, to see its prices, margin, limit and your position."));
			done(item);
			return;
		}
		JsonObject d = Fmt.obj(app(), "item");
		if (d != null && Fmt.num(d, "id") != null && Fmt.num(d, "id").intValue() == id)
		{
			fullItem(d);
		}
		else
		{
			localItem(id);
		}
		done(item);
	}

	private JPanel itemTitle(int id, String name, Double chg)
	{
		JPanel t = Ui.card(BorderFactory.createEmptyBorder(6, 6, 6, 6));
		JLabel ic = new JLabel();
		ic.setPreferredSize(new Dimension(36, 32));
		host.icon(id, 1, ic);
		t.add(ic, BorderLayout.WEST);
		JPanel s = Ui.stack(1);
		s.add(Ui.bold(name));
		s.add(chg == null ? Ui.small("24h change unknown")
			: Ui.change(Fmt.pct(chg, 2) + " in 24 hours", chg, FontManager.getRunescapeSmallFont()));
		t.add(s, BorderLayout.CENTER);
		return t;
	}

	private void fullItem(JsonObject d)
	{
		int id = Fmt.num(d, "id").intValue();
		item.add(itemTitle(id, Fmt.str(d, "name"), Fmt.num(d, "chg24h")));

		JPanel quotes = new JPanel(new GridLayout(1, 2, 6, 0));
		quotes.setOpaque(false);
		quotes.add(quote("Instant buy", Fmt.num(d, "high"), "you sell here"));
		quotes.add(quote("Instant sell", Fmt.num(d, "low"), "you buy here"));
		item.add(quotes);

		JPanel kv = Ui.stack(3);
		Double profit = Fmt.num(d, "profit");
		kv.add(Ui.kv("Margin after tax", profit == null ? Ui.small("-")
			: Ui.change(Fmt.gp(Math.abs(profit)) + " (" + Fmt.pct(Fmt.num(d, "roi"), 2) + ")", profit, FontManager.getRunescapeSmallFont())));
		kv.add(Ui.kv("Tax on a sale", Fmt.gp(Fmt.num(d, "tax"))));
		kv.add(Ui.kv("Offer to buy at", Fmt.gp(Fmt.num(d, "suggestBuy"))));
		kv.add(Ui.kv("Offer to sell at", Fmt.gp(Fmt.num(d, "suggestSell"))));
		Double reset = Fmt.num(d, "limitResetAt");
		String lim = Fmt.gp(Fmt.num(d, "limitLeft")) + " of " + Fmt.gp(Fmt.num(d, "limit"));
		kv.add(Ui.kv("Buy limit left", lim));
		if (reset != null)
		{
			kv.add(Ui.kv("Limit resets in", Fmt.duration(reset - System.currentTimeMillis() / 1000.0)));
		}
		kv.add(Ui.kv("Time to fill a limit", Fmt.hours(Fmt.num(d, "fillHrs"))));
		Double stab = Fmt.num(d, "stability");
		kv.add(Ui.kv("Margin held", stab == null ? "-" : Math.round(stab * 100) + "% of windows"));
		kv.add(Ui.kv("24h volume", Fmt.shortGp(Fmt.num(d, "vol24"))));
		JPanel kvCard = Ui.card(null);
		kvCard.add(kv);
		item.add(kvCard);

		List<double[]> pts = new ArrayList<>();
		for (JsonElement e : Fmt.arr(d, "spark"))
		{
			JsonArray p = e.getAsJsonArray();
			pts.add(new double[]{p.get(0).getAsDouble(), p.get(1).getAsDouble()});
		}
		if (pts.size() > 1)
		{
			item.add(Ui.heading("Last 24 hours"));
			JPanel sp = Ui.card(BorderFactory.createEmptyBorder(4, 4, 4, 4));
			sp.add(new Ui.Sparkline(pts, 44));
			item.add(sp);
		}

		item.add(Ui.heading("Your position"));
		JPanel pos = Ui.card(null);
		JPanel ps = Ui.stack(3);
		Double held = Fmt.num(d, "held");
		if (held == null || held <= 0)
		{
			ps.add(Ui.small("You don't hold any."));
		}
		else
		{
			ps.add(Ui.kv("Held", Fmt.gp(held) + " (" + Fmt.shortGp(Fmt.num(d, "value")) + ")"));
			ps.add(Ui.kv("Average cost", Fmt.gp(Fmt.num(d, "costEach"))));
			ps.add(Ui.kv("Break-even sell", Fmt.gp(Fmt.num(d, "breakeven"))));
			Double pnl = Fmt.num(d, "pnl");
			ps.add(Ui.kv("Unrealized", pnl == null ? Ui.small("-") : Ui.change(Fmt.shortGp(Math.abs(pnl)), pnl, FontManager.getRunescapeSmallFont())));
		}
		for (JsonElement e : Fmt.arr(d, "targets"))
		{
			JsonObject t = e.getAsJsonObject();
			Double q = Fmt.num(t, "qty");
			ps.add(Ui.kv("Target", ("sell".equals(Fmt.str(t, "side")) ? "Sell at " : "Buy at ") + Fmt.gp(Fmt.num(t, "price"))
				+ (q != null ? " (" + Fmt.gp(q) + ")" : "")));
		}
		pos.add(ps);
		item.add(pos);

		if (Fmt.bool(d, "trap"))
		{
			item.add(Ui.wrap("Margin may be a trap: the two prices traded far apart, or recent windows don't show it.", Ui.WARN, 190));
		}
		JsonObject g = Fmt.obj(d, "guard");
		if (g != null)
		{
			StringBuilder why = new StringBuilder();
			for (JsonElement e : Fmt.arr(g, "why"))
			{
				why.append(why.length() > 0 ? "; " : "").append(e.getAsString());
			}
			item.add(Ui.wrap("Manipulation check " + Fmt.num(g, "score").intValue() + "/100: " + why, Ui.WARN, 190));
		}
		JsonArray news = Fmt.arr(d, "news");
		if (news.size() > 0)
		{
			item.add(Ui.heading("In the news"));
			for (JsonElement e : news)
			{
				JsonObject n = e.getAsJsonObject();
				String when = String.format(Locale.US, "%tb %<te", new java.util.Date(Fmt.num(n, "t").longValue() * 1000));
				item.add(Ui.wrap(when + (Fmt.bool(n, "upcoming") ? " (upcoming)" : "") + ": " + Fmt.str(n, "title"), Ui.TEXT, 190));
			}
		}

		JPanel actions = new JPanel(new GridLayout(1, 2, 6, 0));
		actions.setOpaque(false);
		boolean watched = Fmt.bool(d, "watched");
		JButton watch = smallButton(watched ? "Unwatch" : "Watch", watched ? "Remove from your watchlist" : "Add to your watchlist");
		watch.addActionListener(e -> host.setWatched(id, !watched));
		JButton target = smallButton("Set target", "Get an alert when the price reaches a level");
		target.addActionListener(e -> askTarget(id, Fmt.str(d, "name"), Fmt.num(d, "high"), held));
		actions.add(watch);
		actions.add(target);
		item.add(actions);
		JButton term = smallButton("Open in the dashboard", "Open this item in the Terminal");
		term.addActionListener(e -> host.openDashboard("/?item=" + id));
		item.add(term);
	}

	private JPanel quote(String label, Double v, String sub)
	{
		JPanel q = Ui.card(BorderFactory.createEmptyBorder(5, 7, 5, 7));
		JPanel s = Ui.stack(0);
		s.add(Ui.small(label));
		s.add(Ui.label(Fmt.shortGp(v), FontManager.getRunescapeBoldFont().deriveFont(16f), Ui.TEXT));
		s.add(Ui.small(sub));
		q.add(s);
		if (v != null)
		{
			q.setToolTipText(Fmt.gp(v) + " gp");
		}
		return q;
	}

	private void localItem(int id)
	{
		PanelState.Local l = state.local.get(id);
		item.add(itemTitle(id, l != null ? l.name : "Item " + id, null));
		JPanel c = Ui.card(null);
		JPanel s = Ui.stack(3);
		s.add(Ui.kv("RuneLite price", l != null && l.price > 0 ? Fmt.gp((double) l.price) : "-"));
		s.add(Ui.kv("High alch", l != null && l.haPrice > 0 ? Fmt.gp((double) l.haPrice) : "-"));
		c.add(s);
		item.add(c);
		item.add(Ui.wrap(state.status == PanelState.AppStatus.OFFLINE
			? "Instant buy and sell prices, the margin after tax, your buy limit and your position show here when the Bankstanding app is running."
			: "Loading details...", Ui.MUTED, 190));
	}

	private void askTarget(int id, String name, Double high, Double held)
	{
		JComboBox<String> side = new JComboBox<>(new String[]{"Sell when it reaches", "Buy when it drops to"});
		JTextField price = new JTextField(high != null ? Long.toString(Math.round(high * 1.05)) : "");
		JTextField qty = new JTextField(held != null && held > 0 ? Long.toString(held.longValue()) : "");
		JPanel p = new JPanel(new GridLayout(0, 1, 0, 4));
		p.add(new JLabel(name));
		p.add(side);
		p.add(new JLabel("Price (1.2m, 450k or 1,234)"));
		p.add(price);
		p.add(new JLabel("Quantity (optional)"));
		p.add(qty);
		int ok = JOptionPane.showConfirmDialog(this, p, "Bankstanding price target", JOptionPane.OK_CANCEL_OPTION, JOptionPane.PLAIN_MESSAGE);
		if (ok != JOptionPane.OK_OPTION)
		{
			return;
		}
		Long pr = parseGp(price.getText());
		if (pr == null || pr <= 0)
		{
			JOptionPane.showMessageDialog(this, "Enter a price, like 1.2m or 450k.");
			return;
		}
		host.addTarget(id, side.getSelectedIndex() == 0 ? "sell" : "buy", pr, parseGp(qty.getText()));
	}

	/** "1.2m", "450k", "1,234,000" or "2b" to a number; null if empty or not a number. */
	static Long parseGp(String s)
	{
		if (s == null)
		{
			return null;
		}
		String t = s.trim().toLowerCase(Locale.US).replace(",", "").replace(" ", "");
		if (t.isEmpty())
		{
			return null;
		}
		double mult = 1;
		char last = t.charAt(t.length() - 1);
		if (last == 'k' || last == 'm' || last == 'b')
		{
			mult = last == 'k' ? 1e3 : last == 'm' ? 1e6 : 1e9;
			t = t.substring(0, t.length() - 1);
		}
		try
		{
			return Math.round(Double.parseDouble(t) * mult);
		}
		catch (NumberFormatException e)
		{
			return null;
		}
	}

	// Ideas ----------------------------------------------------------------------------------

	private void renderIdeas()
	{
		ideas.removeAll();
		JPanel toggle = new JPanel(new GridLayout(1, 2, 4, 0));
		toggle.setOpaque(false);
		JButton flips = smallButton("Flips", "Flips your cash can fund now");
		JButton watch = smallButton("Watchlist", "Items you watch");
		flips.setForeground(showWatchlist ? Ui.MUTED : Ui.TEXT);
		watch.setForeground(showWatchlist ? Ui.TEXT : Ui.MUTED);
		flips.addActionListener(e ->
		{
			showWatchlist = false;
			renderIdeas();
		});
		watch.addActionListener(e ->
		{
			showWatchlist = true;
			renderIdeas();
		});
		toggle.add(flips);
		toggle.add(watch);
		ideas.add(toggle);
		if (app() == null)
		{
			ideas.add(note("Flip ideas and your watchlist need the Bankstanding app running on this PC."));
			done(ideas);
			return;
		}
		if (showWatchlist)
		{
			JsonArray w = Fmt.arr(app(), "watch");
			if (w.size() == 0)
			{
				ideas.add(note("Nothing watched yet. Use Watch on the Item tab."));
			}
			for (JsonElement e : w)
			{
				JsonObject r = e.getAsJsonObject();
				int id = Fmt.num(r, "id").intValue();
				JPanel row = Ui.card(BorderFactory.createEmptyBorder(5, 6, 5, 7));
				JLabel ic = new JLabel();
				ic.setPreferredSize(new Dimension(32, 28));
				host.icon(id, 1, ic);
				row.add(ic, BorderLayout.WEST);
				JPanel s = Ui.stack(1);
				JPanel top = new JPanel(new BorderLayout());
				top.setOpaque(false);
				top.add(Ui.bold(Fmt.str(r, "name")), BorderLayout.CENTER);
				top.add(Ui.plain(Fmt.shortGp(Fmt.num(r, "high"))), BorderLayout.EAST);
				s.add(top);
				Double chg = Fmt.num(r, "chg24h");
				JPanel bot = new JPanel(new BorderLayout());
				bot.setOpaque(false);
				bot.add(chg == null ? Ui.small("-") : Ui.change(Fmt.pct(chg, 2) + " 24h", chg, FontManager.getRunescapeSmallFont()), BorderLayout.WEST);
				Double pr = Fmt.num(r, "profit");
				bot.add(Ui.small(pr == null ? "" : "margin " + Fmt.shortGp(pr)), BorderLayout.EAST);
				s.add(bot);
				row.add(s, BorderLayout.CENTER);
				Ui.clickable(row, () -> pick(id));
				ideas.add(row);
			}
			done(ideas);
			return;
		}
		JsonObject idea = Fmt.obj(app(), "ideas");
		ideas.add(Ui.wrap("Best flips for " + Fmt.shortGp(Fmt.num(idea, "cash")) + " cash over "
			+ Fmt.num(idea, "freeSlots").intValue() + " free slots. Green is the expected profit per 4 hour limit window, after tax.", Ui.MUTED, 200));
		JsonArray rows = Fmt.arr(idea, "rows");
		if (rows.size() == 0)
		{
			ideas.add(note("No flips fit right now."));
		}
		int rank = 0;
		for (JsonElement e : rows)
		{
			JsonObject r = e.getAsJsonObject();
			int id = Fmt.num(r, "id").intValue();
			JPanel row = Ui.card(BorderFactory.createEmptyBorder(5, 6, 5, 7));
			JLabel num = Ui.small(Integer.toString(++rank));
			num.setPreferredSize(new Dimension(14, 10));
			num.setVerticalAlignment(SwingConstants.TOP);
			row.add(num, BorderLayout.WEST);
			JPanel s = Ui.stack(1);
			JPanel top = new JPanel(new BorderLayout(4, 0));
			top.setOpaque(false);
			top.add(Ui.bold(Fmt.str(r, "name")), BorderLayout.CENTER);
			JLabel per4h = Ui.label(Fmt.shortGp(Fmt.num(r, "adj4h")), FontManager.getRunescapeSmallFont(), Ui.UP);
			per4h.setToolTipText("Expected profit in one 4 hour buy limit window at your fill rate, scaled by margin stability");
			top.add(per4h, BorderLayout.EAST);
			s.add(top);
			s.add(Ui.small("Buy " + Fmt.shortGp(Fmt.num(r, "buy")) + ", sell " + Fmt.shortGp(Fmt.num(r, "sell"))));
			Double stab = Fmt.num(r, "stability");
			JPanel l3 = new JPanel(new BorderLayout(4, 0));
			l3.setOpaque(false);
			l3.add(Ui.small("+" + Fmt.shortGp(Fmt.num(r, "profit")) + " each, " + Fmt.pct(Fmt.num(r, "roi"), 1)), BorderLayout.WEST);
			JLabel fill = Ui.small("fills " + Fmt.hours(Fmt.num(r, "fillHrs")));
			fill.setToolTipText(stab != null ? "The margin held in " + Math.round(stab * 100) + "% of recent 5 minute windows" : null);
			l3.add(fill, BorderLayout.EAST);
			s.add(l3);
			row.add(s, BorderLayout.CENTER);
			Ui.clickable(row, () -> pick(id));
			row.setToolTipText("Show on the Item tab");
			ideas.add(row);
		}
		done(ideas);
	}

	/** Shows the watchlist on the Ideas tab. */
	void ideasWatchlist()
	{
		showWatchlist = true;
		renderIdeas();
		showTab(2);
	}

	private void pick(int id)
	{
		host.selectItem(id);
		showItemTab();
	}

	// Account --------------------------------------------------------------------------------

	private void renderAccount()
	{
		account.removeAll();
		JsonObject h = Fmt.obj(app(), "header");
		JsonObject a = Fmt.obj(app(), "account");
		if (h == null || a == null)
		{
			account.add(note("Your account summary needs the Bankstanding app running on this PC."));
			done(account);
			return;
		}
		JPanel c = Ui.card(null);
		JPanel s = Ui.stack(3);
		s.add(Ui.kv("Cash", Fmt.shortGp(Fmt.num(h, "cash"))));
		s.add(Ui.kv("Items", Fmt.shortGp(Fmt.num(h, "items"))));
		s.add(Ui.kv("In the GE", Fmt.shortGp(Fmt.num(h, "ge"))));
		Double pnl = Fmt.num(h, "pnl");
		s.add(Ui.kv("Unrealized P/L", pnl == null ? Ui.small("-") : Ui.change(Fmt.shortGp(Math.abs(pnl)), pnl, FontManager.getRunescapeSmallFont())));
		c.add(s);
		account.add(c);

		account.add(Ui.heading("Last 24 hours"));
		JPanel d = Ui.card(null);
		JPanel ds = Ui.stack(3);
		Double realized = Fmt.num(a, "realized24");
		ds.add(Ui.kv("Realized profit", realized == null || realized == 0 ? Ui.small("0")
			: Ui.change(Fmt.shortGp(Math.abs(realized)), realized, FontManager.getRunescapeSmallFont())));
		ds.add(Ui.kv("Flips closed", Fmt.gp(Fmt.num(a, "flips24"))));
		for (JsonElement e : Fmt.arr(a, "fills"))
		{
			JsonObject f = e.getAsJsonObject();
			boolean buy = "buy".equals(Fmt.str(f, "side"));
			JLabel l = Ui.label((buy ? "Bought " : "Sold ") + Fmt.gp(Fmt.num(f, "qty")) + " " + Fmt.str(f, "name"),
				FontManager.getRunescapeSmallFont(), Ui.TEXT);
			ds.add(Ui.kv("", l));
			l.setToolTipText(Fmt.gp(Fmt.num(f, "gp")) + " gp");
		}
		d.add(ds);
		account.add(d);

		JsonArray att = Fmt.arr(a, "attention");
		if (att.size() > 0)
		{
			account.add(Ui.heading("Needs attention"));
			for (JsonElement e : att)
			{
				JsonObject n = e.getAsJsonObject();
				JPanel card = Ui.card(BorderFactory.createEmptyBorder(5, 7, 5, 7));
				JPanel ns = Ui.stack(1);
				ns.add(Ui.wrap(Fmt.str(n, "title"), Ui.WARN, 180));
				Double sug = Fmt.num(n, "suggest");
				if (sug != null)
				{
					ns.add(Ui.small("Try " + Fmt.gp(sug)));
				}
				card.add(ns);
				Double iid = Fmt.num(n, "item");
				if (iid != null)
				{
					Ui.clickable(card, () -> pick(iid.intValue()));
				}
				account.add(card);
			}
		}

		JsonArray lims = Fmt.arr(a, "limits");
		if (lims.size() > 0)
		{
			account.add(Ui.heading("Buy limits resetting soon"));
			JPanel lc = Ui.card(null);
			JPanel ls = Ui.stack(3);
			for (JsonElement e : lims)
			{
				JsonObject l = e.getAsJsonObject();
				ls.add(Ui.kv(Fmt.str(l, "name"), "in " + Fmt.duration(Fmt.num(l, "resetAt") - System.currentTimeMillis() / 1000.0)));
			}
			lc.add(ls);
			account.add(lc);
		}

		JsonArray top = Fmt.arr(a, "top");
		if (top.size() > 0)
		{
			account.add(Ui.heading("Biggest holdings"));
			for (JsonElement e : top)
			{
				JsonObject t = e.getAsJsonObject();
				int id = Fmt.num(t, "id").intValue();
				JPanel row = Ui.card(BorderFactory.createEmptyBorder(4, 6, 4, 7));
				JLabel ic = new JLabel();
				ic.setPreferredSize(new Dimension(32, 28));
				host.icon(id, Fmt.num(t, "qty").intValue(), ic);
				row.add(ic, BorderLayout.WEST);
				JPanel rs = Ui.stack(1);
				JPanel r1 = new JPanel(new BorderLayout(4, 0));
				r1.setOpaque(false);
				r1.add(Ui.bold(Fmt.str(t, "name")), BorderLayout.CENTER);
				r1.add(Ui.plain(Fmt.shortGp(Fmt.num(t, "value"))), BorderLayout.EAST);
				rs.add(r1);
				Double p = Fmt.num(t, "pnl");
				Double day = Fmt.num(t, "chg24gp");
				JPanel r2 = new JPanel(new BorderLayout());
				r2.setOpaque(false);
				r2.add(day == null || Math.abs(day) < 1 ? Ui.small("today -") : Ui.change(Fmt.shortGp(Math.abs(day)) + " today", day, FontManager.getRunescapeSmallFont()), BorderLayout.WEST);
				r2.add(p == null ? Ui.small("") : Ui.change("P/L " + Fmt.shortGp(Math.abs(p)), p, FontManager.getRunescapeSmallFont()), BorderLayout.EAST);
				rs.add(r2);
				row.add(rs, BorderLayout.CENTER);
				Ui.clickable(row, () -> pick(id));
				account.add(row);
			}
		}
		account.add(Ui.wrap("Information only. You place every offer yourself.", Ui.MUTED, 190));
		done(account);
	}

	// Plumbing -------------------------------------------------------------------------------

	private static JPanel note(String text)
	{
		JPanel c = Ui.card(BorderFactory.createEmptyBorder(8, 8, 8, 8));
		c.add(Ui.wrap(text, Ui.MUTED, 180));
		return c;
	}

	private static void done(JPanel p)
	{
		p.revalidate();
		p.repaint();
	}

	/** A scroll area whose content always fits the side bar's width. */
	private static JScrollPane scroll(JPanel content)
	{
		Fit view = new Fit();
		view.setLayout(new BorderLayout());
		view.setBackground(Ui.BG);
		view.setBorder(BorderFactory.createEmptyBorder(6, 10, 10, 10));
		view.add(content, BorderLayout.NORTH);
		JScrollPane sp = new JScrollPane(view, JScrollPane.VERTICAL_SCROLLBAR_AS_NEEDED, JScrollPane.HORIZONTAL_SCROLLBAR_NEVER);
		sp.setBorder(BorderFactory.createEmptyBorder());
		sp.getViewport().setBackground(Ui.BG);
		sp.getVerticalScrollBar().setPreferredSize(new Dimension(8, 0));
		sp.getVerticalScrollBar().setUnitIncrement(16);
		return sp;
	}

	private static class Fit extends JPanel implements Scrollable
	{
		@Override
		public Dimension getPreferredScrollableViewportSize()
		{
			return getPreferredSize();
		}

		@Override
		public int getScrollableUnitIncrement(Rectangle r, int o, int d)
		{
			return 16;
		}

		@Override
		public int getScrollableBlockIncrement(Rectangle r, int o, int d)
		{
			return r.height;
		}

		@Override
		public boolean getScrollableTracksViewportWidth()
		{
			return true;
		}

		@Override
		public boolean getScrollableTracksViewportHeight()
		{
			return false;
		}
	}

	/** The toolbar icon: a bank booth whose window is a small candle chart. */
	static BufferedImage icon()
	{
		BufferedImage img = new BufferedImage(16, 16, BufferedImage.TYPE_INT_ARGB);
		Graphics2D g = img.createGraphics();
		g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
		g.setColor(new Color(200, 162, 74));
		g.fillPolygon(new int[]{1, 8, 15}, new int[]{5, 1, 5}, 3);
		g.setColor(new Color(138, 106, 42));
		g.fillRect(1, 5, 14, 1);
		g.fillRect(1, 14, 14, 2);
		g.setColor(new Color(21, 22, 26));
		g.fillRect(2, 6, 12, 8);
		g.setStroke(new BasicStroke(1f));
		g.setColor(Ui.DOWN);
		g.fillRect(4, 10, 2, 3);
		g.setColor(Ui.UP);
		g.drawRect(7, 8, 1, 3);
		g.drawRect(10, 7, 1, 3);
		g.dispose();
		return img;
	}
}
