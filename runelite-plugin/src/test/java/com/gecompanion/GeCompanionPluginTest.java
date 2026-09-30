package com.gecompanion;

import net.runelite.client.RuneLite;
import net.runelite.client.externalplugins.ExternalPluginManager;

/** Starts RuneLite with this plugin loaded (used by ./gradlew run). */
public class GeCompanionPluginTest
{
	public static void main(String[] args) throws Exception
	{
		ExternalPluginManager.loadBuiltin(GeCompanionPlugin.class);
		RuneLite.main(args);
	}
}
