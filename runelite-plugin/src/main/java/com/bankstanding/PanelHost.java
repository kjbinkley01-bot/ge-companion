package com.bankstanding;

import java.util.List;
import java.util.function.Consumer;
import javax.swing.JLabel;

/**
 * What the side panel needs from the plugin. Everything here is display or local app
 * requests: nothing acts on the game.
 */
interface PanelHost
{
	/** Puts the item's icon on the label (loads in the background). */
	void icon(int itemId, int quantity, JLabel target);

	/** Makes this the item shown on the Item tab and refreshes. */
	void selectItem(int itemId);

	/** Finds items by name; results arrive on the Swing thread as [id, name] pairs. */
	void search(String query, Consumer<List<Object[]>> results);

	/** Opens a page of the dashboard (for example "/" or "/?item=385") in the browser. */
	void openDashboard(String path);

	/** Adds or removes the item from the app's watchlist. */
	void setWatched(int itemId, boolean watched);

	/** Saves a price target in the app (it alerts when the price is reached). */
	void addTarget(int itemId, String side, long price, Long quantity);

	/** Asks for fresh data now. */
	void refresh();
}
