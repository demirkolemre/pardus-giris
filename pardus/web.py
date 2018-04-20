#!/usr/bin/env python
# -*- coding:utf-8 -*-
import webkit, gtk, os

win = gtk.Window()
win.resize(600,800)
win.connect('destroy', lambda w: gtk.main_quit())

scroller = gtk.ScrolledWindow()
win.add(scroller)

web = webkit.WebView()
path=os.getcwd()
print path

web.open("file://" + path + "/index.html")

scroller.add(web)

win.show_all()

gtk.main()