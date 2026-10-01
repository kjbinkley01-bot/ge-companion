package com.bankstanding;

import java.awt.BasicStroke;
import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Component;
import java.awt.Cursor;
import java.awt.Dimension;
import java.awt.Font;
import java.awt.Graphics;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.awt.geom.Path2D;
import java.util.List;
import javax.swing.BorderFactory;
import javax.swing.Icon;
import javax.swing.JComponent;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.border.Border;
import net.runelite.client.ui.ColorScheme;
import net.runelite.client.ui.DynamicGridLayout;
import net.runelite.client.ui.FontManager;

/** Small building blocks for the side panel, styled like RuneLite's own panels. */
final class Ui
{
	/** Up and down use Bankstanding's dashboard colors, always with a triangle as well. */
	static final Color UP = new Color(0xCC, 0xFF, 0x00);
	static final Color DOWN = new Color(0xFF, 0x4D, 0x4A);
	static final Color WARN = new Color(0xF5, 0xB4, 0x00);
	static final Color MUTED = new Color(0x9A, 0x9E, 0xA5);
	static final Color TEXT = Color.WHITE;
	static final Color CARD = ColorScheme.DARKER_GRAY_COLOR;
	static final Color CARD_HOVER = ColorScheme.DARKER_GRAY_HOVER_COLOR;
	static final Color BG = ColorScheme.DARK_GRAY_COLOR;
	/** Content width inside the side bar (panel width minus the scroll bar and padding). */
	static final int WIDTH = 205;

	private Ui()
	{
	}

	static JLabel label(String text, Font font, Color color)
	{
		JLabel l = new JLabel(text);
		l.setFont(font);
		l.setForeground(color);
		return l;
	}

	static JLabel small(String text)
	{
		return label(text, FontManager.getRunescapeSmallFont(), MUTED);
	}

	static JLabel bold(String text)
	{
		return label(text, FontManager.getRunescapeBoldFont(), TEXT);
	}

	static JLabel plain(String text)
	{
		return label(text, FontManager.getRunescapeFont(), TEXT);
	}

	/** Wrapped text (HTML) limited to about `width` screen pixels. */
	static JLabel wrap(String text, Color color, int width)
	{
		// Swing's HTML treats CSS pixels as points, so scale them back to screen pixels.
		int css = (int) Math.round(width * 72.0 / DPI);
		return label("<html><div style='width:" + css + "px'>" + Fmt.esc(text) + "</div></html>",
			FontManager.getRunescapeSmallFont(), color);
	}

	private static final int DPI = screenDpi();

	private static int screenDpi()
	{
		try
		{
			int d = java.awt.Toolkit.getDefaultToolkit().getScreenResolution();
			return d >= 72 && d <= 300 ? d : 96;
		}
		catch (Exception | Error e)
		{
			return 96;
		}
	}

	/** A label showing a value with an up or down triangle in front of it. */
	static JLabel change(String text, Double sign, Font font)
	{
		JLabel l = label(text, font, sign == null || sign == 0 ? MUTED : sign > 0 ? UP : DOWN);
		if (sign != null && sign != 0)
		{
			l.setIcon(new Triangle(sign > 0, l.getForeground()));
			l.setIconTextGap(3);
		}
		return l;
	}

	/** A vertical list of full width rows with a gap between them. */
	static JPanel stack(int gap)
	{
		JPanel p = new JPanel(new DynamicGridLayout(0, 1, 0, gap));
		p.setOpaque(false);
		return p;
	}

	/** Stops a stack from stretching to fill a tall scroll area. */
	static JPanel top(JComponent c)
	{
		JPanel p = new JPanel(new BorderLayout());
		p.setBackground(BG);
		p.add(c, BorderLayout.NORTH);
		return p;
	}

	/** A card: darker background with padding. */
	static JPanel card(Border padding)
	{
		JPanel p = new JPanel(new BorderLayout(6, 2));
		p.setBackground(CARD);
		p.setBorder(padding == null ? BorderFactory.createEmptyBorder(6, 7, 6, 7) : padding);
		return p;
	}

	/** Key on the left, value on the right. */
	static JPanel kv(String key, JComponent value)
	{
		JPanel p = new JPanel(new BorderLayout(6, 0));
		p.setOpaque(false);
		p.add(small(key), BorderLayout.WEST);
		p.add(value, BorderLayout.EAST);
		return p;
	}

	static JPanel kv(String key, String value)
	{
		return kv(key, label(value, FontManager.getRunescapeSmallFont(), TEXT));
	}

	static JLabel heading(String text)
	{
		JLabel l = label(text.toUpperCase(), FontManager.getRunescapeSmallFont(), MUTED);
		l.setBorder(BorderFactory.createEmptyBorder(6, 1, 0, 0));
		return l;
	}

	/** Makes a whole component clickable, with a hover color. */
	static void clickable(JComponent c, Runnable action)
	{
		c.setCursor(Cursor.getPredefinedCursor(Cursor.HAND_CURSOR));
		Color base = c.getBackground();
		c.addMouseListener(new MouseAdapter()
		{
			@Override
			public void mouseClicked(MouseEvent e)
			{
				action.run();
			}

			@Override
			public void mouseEntered(MouseEvent e)
			{
				c.setBackground(CARD_HOVER);
			}

			@Override
			public void mouseExited(MouseEvent e)
			{
				c.setBackground(base);
			}
		});
	}

	static void fixWidth(Component c, int w)
	{
		Dimension d = c.getPreferredSize();
		c.setPreferredSize(new Dimension(w, d.height));
	}

	/** A small filled triangle pointing up or down. */
	static class Triangle implements Icon
	{
		private final boolean up;
		private final Color color;

		Triangle(boolean up, Color color)
		{
			this.up = up;
			this.color = color;
		}

		@Override
		public void paintIcon(Component c, Graphics g, int x, int y)
		{
			Graphics2D g2 = (Graphics2D) g.create();
			g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
			g2.setColor(color);
			Path2D p = new Path2D.Double();
			if (up)
			{
				p.moveTo(x, y + 6);
				p.lineTo(x + 7, y + 6);
				p.lineTo(x + 3.5, y);
			}
			else
			{
				p.moveTo(x, y);
				p.lineTo(x + 7, y);
				p.lineTo(x + 3.5, y + 6);
			}
			p.closePath();
			g2.fill(p);
			g2.dispose();
		}

		@Override
		public int getIconWidth()
		{
			return 7;
		}

		@Override
		public int getIconHeight()
		{
			return 6;
		}
	}

	/** A small round status light. */
	static class Dot implements Icon
	{
		private final Color color;

		Dot(Color color)
		{
			this.color = color;
		}

		@Override
		public void paintIcon(Component c, Graphics g, int x, int y)
		{
			Graphics2D g2 = (Graphics2D) g.create();
			g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
			g2.setColor(color);
			g2.fillOval(x, y + 1, 7, 7);
			g2.dispose();
		}

		@Override
		public int getIconWidth()
		{
			return 7;
		}

		@Override
		public int getIconHeight()
		{
			return 9;
		}
	}

	/** A price line over the last day: lime if it ended higher, red if lower, dotted start level. */
	static class Sparkline extends JComponent
	{
		private final List<double[]> pts;

		Sparkline(List<double[]> pts, int height)
		{
			this.pts = pts;
			setPreferredSize(new Dimension(WIDTH, height));
		}

		@Override
		protected void paintComponent(Graphics g)
		{
			if (pts.size() < 2)
			{
				return;
			}
			Graphics2D g2 = (Graphics2D) g.create();
			g2.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
			int w = getWidth(), h = getHeight();
			double t0 = pts.get(0)[0], t1 = pts.get(pts.size() - 1)[0];
			double lo = Double.MAX_VALUE, hi = -Double.MAX_VALUE;
			for (double[] p : pts)
			{
				lo = Math.min(lo, p[1]);
				hi = Math.max(hi, p[1]);
			}
			if (hi - lo < hi * 0.002)
			{
				double mid = (hi + lo) / 2;
				lo = mid - hi * 0.001;
				hi = mid + hi * 0.001;
			}
			final double flo = lo, fhi = hi;
			java.util.function.DoubleUnaryOperator y = v -> h - 3 - (v - flo) / (fhi - flo) * (h - 6);
			java.util.function.DoubleUnaryOperator x = t -> 1 + (t - t0) / Math.max(1, t1 - t0) * (w - 2);
			double first = pts.get(0)[1], last = pts.get(pts.size() - 1)[1];
			g2.setColor(new Color(255, 255, 255, 60));
			g2.setStroke(new BasicStroke(1f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_MITER, 1f, new float[]{1f, 3f}, 0f));
			int yb = (int) Math.round(y.applyAsDouble(first));
			g2.drawLine(0, yb, w, yb);
			Path2D path = new Path2D.Double();
			for (int i = 0; i < pts.size(); i++)
			{
				double px = x.applyAsDouble(pts.get(i)[0]), py = y.applyAsDouble(pts.get(i)[1]);
				if (i == 0)
				{
					path.moveTo(px, py);
				}
				else
				{
					path.lineTo(px, py);
				}
			}
			g2.setColor(last >= first ? UP : DOWN);
			g2.setStroke(new BasicStroke(1.5f, BasicStroke.CAP_ROUND, BasicStroke.JOIN_ROUND));
			g2.draw(path);
			g2.dispose();
		}
	}
}
