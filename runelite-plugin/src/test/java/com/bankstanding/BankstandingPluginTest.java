package com.bankstanding;

import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;

/** Starts RuneLite with this plugin loaded (used by ./gradlew run). */
public class BankstandingPluginTest
{
	public static void main(String[] args) throws Exception
	{
		ExternalPluginManager.loadBuiltin(BankstandingPlugin.class);
		RuneLite.main(args);
	}
}
