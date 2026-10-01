package com.bankstanding;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import java.awt.Dimension;
import java.awt.Graphics2D;
import javax.inject.Inject;
import net.runelite.api.Client;
import net.runelite.api.MenuEntry;
import net.runelite.api.gameval.InterfaceID;
import net.runelite.api.widgets.Widget;
import net.runelite.api.widgets.WidgetUtil;
import net.runelite.client.game.ItemManager;
import net.runelite.client.ui.overlay.Overlay;
import net.runelite.client.ui.overlay.OverlayLayer;
import net.runelite.client.ui.overlay.OverlayPosition;
import net.runelite.client.ui.overlay.tooltip.Tooltip;
import net.runelite.client.ui.overlay.tooltip.TooltipManager;
import net.runelite.client.util.ColorUtil;

/**
 * When the mouse is over an item in the bank, adds a tooltip with your average cost, its value
 * now and the profit or loss on what you hold. Display only.
 */
class BankCostOverlay extends Overlay
{
	private final Client client;
	private final ItemManager itemManager;
	private final TooltipManager tooltipManager;
	/** {itemId: [quantity, average cost each, value each now]} from the app. */
	private volatile JsonObject costs;

	@Inject
	BankCostOverlay(Client client, ItemManager itemManager, TooltipManager tooltipManager)
	{
		this.client = client;
		this.itemManager = itemManager;
		this.tooltipManager = tooltipManager;
		setPosition(OverlayPosition.DYNAMIC);
		setLayer(OverlayLayer.ABOVE_WIDGETS);
	}

	void setCosts(JsonObject c)
	{
		costs = c;
	}

	@Override
	public Dimension render(Graphics2D g)
	{
		JsonObject c = costs;
		if (c == null || client.isMenuOpen())
		{
			return null;
		}
		MenuEntry[] entries = client.getMenu().getMenuEntries();
		if (entries == null || entries.length == 0)
		{
			return null;
		}
		MenuEntry top = entries[entries.length - 1];
		Widget w = top.getWidget();
		if (w == null || WidgetUtil.componentToInterface(w.getId()) != InterfaceID.BANKMAIN)
		{
			return null;
		}
		int id = w.getItemId();
		if (id <= 0)
		{
			return null;
		}
		String text = tooltip(c, itemManager.canonicalize(id));
		if (text != null)
		{
			tooltipManager.add(new Tooltip(text));
		}
		return null;
	}

	/** The tooltip text for one item, or null when there is no cost for it. */
	static String tooltip(JsonObject costs, int id)
	{
		JsonElement e = costs.get(Integer.toString(id));
		if (e == null || !e.isJsonArray())
		{
			return null;
		}
		JsonArray a = e.getAsJsonArray();
		if (a.size() < 2 || a.get(1).isJsonNull())
		{
			return null;
		}
		double qty = a.get(0).getAsDouble(), cost = a.get(1).getAsDouble();
		StringBuilder sb = new StringBuilder("Bankstanding</br>Average cost: " + Fmt.gp(cost));
		if (a.size() > 2 && !a.get(2).isJsonNull())
		{
			double now = a.get(2).getAsDouble();
			double pnl = (now - cost) * qty;
			sb.append("</br>Now: ").append(Fmt.gp(now));
			sb.append("</br>P/L: ").append(ColorUtil.wrapWithColorTag((pnl >= 0 ? "+" : "-") + Fmt.shortGp(Math.abs(pnl)),
				pnl >= 0 ? Ui.UP : Ui.DOWN)).append(" on ").append(Fmt.gp(qty));
		}
		return sb.toString();
	}
}
