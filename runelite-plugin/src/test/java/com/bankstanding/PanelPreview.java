package com.bankstanding;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.awt.Dimension;
import java.awt.Graphics2D;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import javax.imageio.ImageIO;
import javax.swing.ImageIcon;
import javax.swing.JFrame;
import javax.swing.JLabel;
import javax.swing.SwingUtilities;
import net.runelite.client.ui.laf.RuneLiteLAF;

/**
 * Renders the side panel outside the game, with data from a running Bankstanding app (demo
 * mode works), and saves one PNG per tab. Not a test: run with
 * {@code gradlew previewPanel -Papp=http://127.0.0.1:8799 -Pout=build/preview}.
 */
public class PanelPreview
{
	private static final Gson GSON = new Gson();
	private static final Map<Integer, String> ICONS = new HashMap<>();
	private static final Map<Integer, String> NAMES = new HashMap<>();
	private static final Map<Integer, BufferedImage> CACHE = new HashMap<>();

	public static void main(String[] args) throws Exception
	{
		String app = args.length > 0 ? args[0] : "http://127.0.0.1:8799";
		File out = new File(args.length > 1 ? args[1] : "build/preview");
		out.mkdirs();
		for (JsonElement e : get(app + "/api/market").getAsJsonObject().getAsJsonArray("rows"))
		{
			JsonObject r = e.getAsJsonObject();
			ICONS.put(r.get("id").getAsInt(), r.has("icon") && !r.get("icon").isJsonNull() ? r.get("icon").getAsString() : null);
			NAMES.put(r.get("id").getAsInt(), r.get("name").getAsString());
		}
		String acct = get(app + "/api/account/status").getAsJsonObject().getAsJsonArray("accounts").get(0).getAsJsonObject().get("acct").getAsString();

		// The demo account's GE slots as the game would report them, plus one finished offer.
		PanelState st = new PanelState();
		st.loggedIn = true;
		st.selectedItem = 385;
		JsonArray orders = get(app + "/api/account/offers?acct=" + URLEncoder.encode(acct, "UTF-8")).getAsJsonObject().getAsJsonArray("orders");
		Map<Integer, PanelState.Slot> bySlot = new HashMap<>();
		for (JsonElement e : orders)
		{
			JsonObject o = e.getAsJsonObject();
			if (!"Working".equals(o.get("status").getAsString()))
			{
				continue;
			}
			PanelState.Slot s = new PanelState.Slot();
			s.slot = o.get("slot").getAsInt();
			s.itemId = o.get("item").getAsInt();
			s.name = o.get("name").getAsString();
			boolean buy = "buy".equals(o.get("side").getAsString());
			s.state = buy ? "BUYING" : "SELLING";
			s.price = o.get("price").getAsLong();
			s.total = o.get("qty").getAsInt();
			s.done = o.get("done").getAsInt();
			s.spent = s.done * s.price;
			bySlot.put(s.slot, s);
		}
		PanelState.Slot fin = new PanelState.Slot();
		fin.slot = 4;
		fin.itemId = 2434;
		fin.name = NAMES.get(2434);
		fin.state = "BOUGHT";
		fin.price = 9900;
		fin.total = 2000;
		fin.done = 2000;
		fin.spent = 19_740_000;
		bySlot.put(4, fin);
		StringBuilder ids = new StringBuilder();
		for (int i = 0; i < 8; i++)
		{
			PanelState.Slot s = bySlot.get(i);
			if (s == null)
			{
				s = new PanelState.Slot();
				s.slot = i;
			}
			else
			{
				ids.append(ids.length() > 0 ? "," : "").append(s.itemId);
			}
			st.slots.add(s);
		}
		st.app = get(app + "/api/plugin/panel?acct=" + URLEncoder.encode(acct, "UTF-8") + "&item=385&slots=" + ids).getAsJsonObject();
		st.appTime = System.currentTimeMillis();
		st.status = PanelState.AppStatus.LIVE;
		for (int i = 0; i < 8; i++)
		{
			icon(i == 0 ? 385 : 0);
		}

		PanelHost host = new PanelHost()
		{
			@Override
			public void icon(int itemId, int quantity, JLabel target)
			{
				BufferedImage img = PanelPreview.icon(itemId);
				if (img != null)
				{
					target.setIcon(new ImageIcon(img));
				}
			}

			@Override
			public void selectItem(int itemId)
			{
			}

			@Override
			public void search(String query, Consumer<List<Object[]>> results)
			{
				List<Object[]> r = new ArrayList<>();
				for (Map.Entry<Integer, String> n : NAMES.entrySet())
				{
					if (n.getValue().toLowerCase().contains(query.toLowerCase()) && r.size() < 8)
					{
						r.add(new Object[]{n.getKey(), n.getValue()});
					}
				}
				results.accept(r);
			}

			@Override
			public void openDashboard(String path)
			{
			}

			@Override
			public void setWatched(int itemId, boolean watched)
			{
			}

			@Override
			public void addTarget(int itemId, String side, long price, Long quantity)
			{
			}

			@Override
			public void refresh()
			{
			}
		};

		PanelState offline = new PanelState();
		offline.loggedIn = true;
		offline.slots.addAll(st.slots);
		offline.selectedItem = 385;
		for (PanelState.Slot s : st.slots)
		{
			if (s.itemId > 0)
			{
				PanelState.Local l = new PanelState.Local();
				l.name = s.name;
				JsonObject p = st.app.getAsJsonObject("prices").getAsJsonObject(Integer.toString(s.itemId));
				l.price = p != null && p.has("high") && !p.get("high").isJsonNull() ? p.get("high").getAsLong() : 0;
				offline.local.put(s.itemId, l);
			}
		}
		PanelState.Local shark = new PanelState.Local();
		shark.name = "Shark";
		shark.price = 1007;
		shark.haPrice = 180;
		offline.local.put(385, shark);

		final BankstandingPanel[] holder = new BankstandingPanel[1];
		final JFrame[] frame = new JFrame[1];
		SwingUtilities.invokeAndWait(() ->
		{
			RuneLiteLAF.setup();
			holder[0] = new BankstandingPanel(host);
			frame[0] = new JFrame("Bankstanding panel preview");
			frame[0].setUndecorated(true);
			frame[0].setContentPane(holder[0]);
			Dimension d = holder[0].getPreferredSize();
			frame[0].setSize(Math.max(d.width, 242), 980);
			frame[0].setVisible(true);
		});
		String[] names = {"1-offers", "2-item", "3-ideas", "4-account"};
		for (int tab = 0; tab < 4; tab++)
		{
			int t = tab;
			SwingUtilities.invokeAndWait(() ->
			{
				holder[0].update(st);
				holder[0].showTab(t);
			});
			shot(frame[0], new File(out, names[tab] + ".png"));
		}
		SwingUtilities.invokeAndWait(() -> holder[0].ideasWatchlist());
		shot(frame[0], new File(out, "3b-watchlist.png"));
		SwingUtilities.invokeAndWait(() ->
		{
			holder[0].update(offline);
			holder[0].showTab(0);
		});
		shot(frame[0], new File(out, "5-offline-offers.png"));
		SwingUtilities.invokeAndWait(() -> holder[0].showTab(1));
		shot(frame[0], new File(out, "6-offline-item.png"));
		frame[0].dispose();
		System.exit(0);
	}

	private static void shot(JFrame f, File file) throws Exception
	{
		Thread.sleep(600);
		BufferedImage[] img = new BufferedImage[1];
		SwingUtilities.invokeAndWait(() ->
		{
			f.getContentPane().validate();
			img[0] = new BufferedImage(f.getWidth(), f.getHeight(), BufferedImage.TYPE_INT_RGB);
			Graphics2D g = img[0].createGraphics();
			f.getContentPane().paint(g);
			g.dispose();
		});
		ImageIO.write(img[0], "png", file);
	}

	static BufferedImage icon(int id)
	{
		if (CACHE.containsKey(id))
		{
			return CACHE.get(id);
		}
		BufferedImage img = null;
		String icon = ICONS.get(id);
		if (icon != null)
		{
			try
			{
				URL u = new URL("https://oldschool.runescape.wiki/images/" + URLEncoder.encode(icon.replace(' ', '_'), "UTF-8").replace("%28", "(").replace("%29", ")"));
				HttpURLConnection c = (HttpURLConnection) u.openConnection();
				c.setRequestProperty("User-Agent", "Bankstanding panel preview");
				try (InputStream in = c.getInputStream())
				{
					img = ImageIO.read(in);
				}
			}
			catch (Exception e)
			{
				img = null;
			}
		}
		CACHE.put(id, img);
		return img;
	}

	private static JsonElement get(String url) throws Exception
	{
		HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
		try (InputStreamReader r = new InputStreamReader(c.getInputStream(), StandardCharsets.UTF_8))
		{
			return GSON.fromJson(r, JsonElement.class);
		}
	}
}
