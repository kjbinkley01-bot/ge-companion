package com.bankstanding;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNull;
import static org.junit.Assert.assertTrue;

import com.google.gson.JsonObject;
import org.junit.Test;

public class PanelLogicTest
{
	@Test
	public void parsesGpAmounts()
	{
		assertEquals(Long.valueOf(1_200_000), BankstandingPanel.parseGp("1.2m"));
		assertEquals(Long.valueOf(450_000), BankstandingPanel.parseGp(" 450K "));
		assertEquals(Long.valueOf(1_234_000), BankstandingPanel.parseGp("1,234,000"));
		assertEquals(Long.valueOf(2_000_000_000L), BankstandingPanel.parseGp("2b"));
		assertNull(BankstandingPanel.parseGp(""));
		assertNull(BankstandingPanel.parseGp("lots"));
	}

	@Test
	public void formatsShortGp()
	{
		assertEquals("1.76b", Fmt.shortGp(1_760_631_666.0));
		assertEquals("209.7m", Fmt.shortGp(209_721_040.0));
		assertEquals("2.56m", Fmt.shortGp(2_560_000.0));
		assertEquals("845k", Fmt.shortGp(845_000.0));
		assertEquals("9,902", Fmt.shortGp(9902.0));
		assertEquals("-1.22m", Fmt.shortGp(-1_220_000.0));
		assertEquals("43 min", Fmt.hours(0.72));
		assertEquals("2.7h", Fmt.hours(2.72));
	}

	@Test
	public void bankTooltipShowsCostAndProfit()
	{
		JsonObject costs = new com.google.gson.Gson().fromJson("{\"385\": [2572, 984.3, 1003.0], \"4151\": [1, 800000.0, null]}", JsonObject.class);
		String t = BankCostOverlay.tooltip(costs, 385);
		assertTrue(t, t.contains("Average cost: 984"));
		assertTrue(t, t.contains("Now: 1,003"));
		assertTrue(t, t.contains("+48k") || t.contains("+48,"));
		String w = BankCostOverlay.tooltip(costs, 4151);
		assertTrue(w, w.contains("800,000") && !w.contains("P/L"));
		assertNull(BankCostOverlay.tooltip(costs, 1));
	}

	@Test
	public void slotStates()
	{
		PanelState.Slot s = new PanelState.Slot();
		assertTrue(s.empty());
		s.itemId = 385;
		s.state = "BOUGHT";
		assertTrue(s.buy() && s.finished() && !s.empty());
		s.state = "SELLING";
		assertTrue(!s.buy() && !s.finished());
	}
}
