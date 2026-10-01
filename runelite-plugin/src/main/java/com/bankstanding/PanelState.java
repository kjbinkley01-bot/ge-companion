package com.bankstanding;

import com.google.gson.JsonObject;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** One snapshot for the side panel: the game's own GE slots plus the app's answer. */
class PanelState
{
	enum AppStatus
	{
		/** The app answered and its prices are fresh. */
		LIVE,
		/** The app answered but its prices are old, or the last answer is a few minutes old. */
		STALE,
		/** No answer from the app. */
		OFFLINE
	}

	/** A GE slot as the game client reports it. */
	static class Slot
	{
		int slot;
		int itemId;
		String name;
		/** EMPTY, BUYING, BOUGHT, SELLING, SOLD, CANCELLED_BUY, CANCELLED_SELL. */
		String state = "EMPTY";
		long price;
		int total;
		int done;
		long spent;

		boolean empty()
		{
			return itemId <= 0 || "EMPTY".equals(state);
		}

		boolean buy()
		{
			return state.contains("BUY") || "BOUGHT".equals(state);
		}

		boolean finished()
		{
			return "BOUGHT".equals(state) || "SOLD".equals(state) || state.startsWith("CANCELLED");
		}
	}

	/** What RuneLite itself knows about an item (works without the app). */
	static class Local
	{
		String name;
		/** RuneLite's Wiki based price, 0 if unknown. */
		long price;
		int haPrice;
	}

	final List<Slot> slots = new ArrayList<>();
	final Map<Integer, Local> local = new HashMap<>();
	/** The app's /api/plugin/panel answer, or null. */
	JsonObject app;
	AppStatus status = AppStatus.OFFLINE;
	/** When the app last answered (ms), 0 if never. */
	long appTime;
	boolean loggedIn;
	int selectedItem = -1;
}
