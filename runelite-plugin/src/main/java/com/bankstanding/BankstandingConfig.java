package com.bankstanding;

import net.runelite.client.config.Config;
import net.runelite.client.config.ConfigGroup;
import net.runelite.client.config.ConfigItem;

@ConfigGroup(BankstandingConfig.GROUP)
public interface BankstandingConfig extends Config
{
	String GROUP = "bankstanding";

	@ConfigItem(
		keyName = "recordOffers",
		name = "Grand Exchange offers",
		description = "Record every change to your GE offers (the dashboard builds your trades and flips from these)",
		position = 1
	)
	default boolean recordOffers()
	{
		return true;
	}

	@ConfigItem(
		keyName = "recordContainers",
		name = "Bank, inventory and gear",
		description = "Record what you hold (bank when opened, inventory, equipment, looting bag, seed vault, rune pouch, GE collection boxes) for net worth",
		position = 2
	)
	default boolean recordContainers()
	{
		return true;
	}

	@ConfigItem(
		keyName = "recordLoot",
		name = "Loot",
		description = "Record loot from the Loot Tracker (kills, raids, clues) for the drop log",
		position = 3
	)
	default boolean recordLoot()
	{
		return true;
	}

	@ConfigItem(
		keyName = "recordXp",
		name = "Experience",
		description = "Record XP changes (at most once a minute per skill)",
		position = 4
	)
	default boolean recordXp()
	{
		return true;
	}

	@ConfigItem(
		keyName = "outputFolder",
		name = "Output folder",
		description = "Where event files are written. Blank means .runelite/bankstanding in your home folder",
		position = 5
	)
	default String outputFolder()
	{
		return "";
	}

	@ConfigItem(
		keyName = "showPanel",
		name = "Show side panel",
		description = "A side panel with your net worth, GE slots with tips, item prices, flip ideas and your account (reads the Bankstanding app on this computer; display only)",
		position = 6
	)
	default boolean showPanel()
	{
		return true;
	}

	@ConfigItem(
		keyName = "appUrl",
		name = "App address",
		description = "Where the Bankstanding app runs on this computer",
		position = 7
	)
	default String appUrl()
	{
		return "http://127.0.0.1:8765";
	}

	@ConfigItem(
		keyName = "followGeItem",
		name = "Follow the GE screen",
		description = "When you pick an item on the Grand Exchange offer screen, show it on the side panel's Item tab",
		position = 8
	)
	default boolean followGeItem()
	{
		return true;
	}

	@ConfigItem(
		keyName = "notifyAlerts",
		name = "Notify alerts",
		description = "Show a RuneLite notification when the app raises an alert (price targets, stalled offers, limit resets)",
		position = 9
	)
	default boolean notifyAlerts()
	{
		return true;
	}

	@ConfigItem(
		keyName = "bankTooltip",
		name = "Cost in bank tooltip",
		description = "When hovering an item in the bank, show your average cost, its value now and your profit or loss",
		position = 10
	)
	default boolean bankTooltip()
	{
		return true;
	}
}
