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
}
