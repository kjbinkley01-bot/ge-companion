package com.bankstanding;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.awt.BasicStroke;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.text.NumberFormat;
import java.util.Locale;
import java.util.function.Supplier;
import javax.swing.BorderFactory;
import javax.swing.JButton;
import javax.swing.JLabel;
import javax.swing.SwingConstants;
import net.runelite.client.ui.ColorScheme;
import net.runelite.client.ui.PluginPanel;
import net.runelite.client.util.LinkBrowser;

/**
 * Side panel that shows what the Bankstanding app knows: net worth, the item open on the GE
 * offer screen with suggested prices, and offers that need attention. Display only: it never
 * clicks, types or changes anything in the game.
 */
class BankstandingPanel extends PluginPanel
{
	private static final Color UP = new Color(0, 200, 5);
	private static final Color DOWN = new Color(255, 80, 0);
	private final JLabel body = new JLabel();

	BankstandingPanel(Supplier<String> appUrl)
	{
		setLayout(new BorderLayout(0, 10));
		setBorder(BorderFactory.createEmptyBorder(10, 10, 10, 10));
		setBackground(ColorScheme.DARK_GRAY_COLOR);
		// Opens the dashboard in the web browser (a link, like the Wiki button elsewhere).
		JButton open = new JButton("Open dashboard");
		open.setFocusable(false);
		open.addActionListener(e -> LinkBrowser.browse(appUrl.get().trim()));
		add(open, BorderLayout.NORTH);
		body.setVerticalAlignment(SwingConstants.TOP);
		body.setForeground(Color.WHITE);
		add(body, BorderLayout.CENTER);
		showMessage("Waiting for the Bankstanding app...");
	}

	void showMessage(String text)
	{
		body.setText("<html><div style='width:190px'><b style='font-size:12px'>Bankstanding</b><br><br>"
			+ esc(text) + "</div></html>");
	}

	/** Renders the /api/plugin/summary response. Called on the Swing thread. */
	void show(JsonObject s)
	{
		StringBuilder h = new StringBuilder("<html><div style='width:190px'>");
		h.append("<b style='font-size:12px'>Bankstanding</b><br><br>");
		h.append("<span style='color:#9a9ea5'>NET WORTH</span><br>");
		h.append("<b style='font-size:15px'>").append(gp(num(s, "total"))).append("</b>");
		Double d1 = num(s, "d1");
		if (d1 != null)
		{
			h.append("<br><span style='color:").append(hex(d1 >= 0 ? UP : DOWN)).append("'>")
				.append(d1 >= 0 ? "+" : "-").append(gp(Math.abs(d1))).append(" today</span>");
		}
		h.append("<br><span style='color:#9a9ea5'>Cash ").append(gp(num(s, "cash"))).append("</span>");
		JsonElement it = s.get("item");
		if (it != null && it.isJsonObject())
		{
			JsonObject i = it.getAsJsonObject();
			h.append("<br><br><span style='color:#9a9ea5'>ON THE GE SCREEN</span><br><b>").append(esc(str(i, "name"))).append("</b>");
			row(h, "Instant buy", gp(num(i, "high")));
			row(h, "Instant sell", gp(num(i, "low")));
			row(h, "Offer to buy at", gp(num(i, "suggestBuy")));
			row(h, "Offer to sell at", gp(num(i, "suggestSell")));
			Double profit = num(i, "profit");
			if (profit != null)
			{
				row(h, "Flip profit each", (profit >= 0 ? "+" : "") + NumberFormat.getIntegerInstance(Locale.US).format(profit));
			}
			row(h, "Your limit left", gp(num(i, "limitLeft")));
			Double fill = num(i, "fillHrs");
			if (fill != null)
			{
				row(h, "Fill time (limit)", String.format(Locale.US, "%.1fh", fill));
			}
			Double held = num(i, "held");
			if (held != null && held > 0)
			{
				Double cost = num(i, "costEach");
				row(h, "You hold", gp(held) + (cost != null ? " at " + gp(cost) : ""));
			}
		}
		JsonElement coach = s.get("coach");
		if (coach != null && coach.isJsonArray() && coach.getAsJsonArray().size() > 0)
		{
			h.append("<br><br><span style='color:#9a9ea5'>NEEDS ATTENTION</span>");
			JsonArray arr = coach.getAsJsonArray();
			for (JsonElement e : arr)
			{
				JsonObject c = e.getAsJsonObject();
				h.append("<br><span style='color:#f5b400'>").append(esc(str(c, "title"))).append("</span>");
				Double sug = num(c, "suggest");
				if (sug != null)
				{
					h.append("<br><span style='color:#9a9ea5'>Try ").append(gp(sug)).append("</span>");
				}
			}
		}
		h.append("<br><br><span style='color:#6b7078'>Information only. You place every offer yourself.</span>");
		h.append("</div></html>");
		body.setText(h.toString());
	}

	private static void row(StringBuilder h, String k, String v)
	{
		h.append("<br><span style='color:#9a9ea5'>").append(esc(k)).append(":</span> ").append(esc(v));
	}

	private static Double num(JsonObject o, String k)
	{
		JsonElement e = o.get(k);
		return e == null || e.isJsonNull() ? null : e.getAsDouble();
	}

	private static String str(JsonObject o, String k)
	{
		JsonElement e = o.get(k);
		return e == null || e.isJsonNull() ? "" : e.getAsString();
	}

	static String gp(Double v)
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
		if (a >= 1e6)
		{
			return s + String.format(Locale.US, "%.1fm", a / 1e6);
		}
		if (a >= 1e5)
		{
			return s + String.format(Locale.US, "%.0fk", a / 1e3);
		}
		return s + NumberFormat.getIntegerInstance(Locale.US).format(a);
	}

	private static String hex(Color c)
	{
		return String.format("#%02x%02x%02x", c.getRed(), c.getGreen(), c.getBlue());
	}

	private static String esc(String s)
	{
		return s == null ? "" : s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;");
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
		g.setColor(DOWN);
		g.fillRect(4, 10, 2, 3);
		g.setColor(UP);
		g.drawRect(7, 8, 1, 3);
		g.drawRect(10, 7, 1, 3);
		g.dispose();
		return img;
	}
}
