=======
Peprika
=======

Peprika is a python code formatter that tries to improve the layout of code to
better conform to the PEP8 standards.  It works by first tokenising and then
rebuiding the code.  Peprika aims to work with the developer rather than against
them.

Features
========

* Correct spacing between operators, variables and keywords.

* Remove dirty whitespace, option to pad blanklines with whitespace.

* Converts code to use single or double quotes as preference.

* Remove Unnecessary blank lines as well as adding ones for PEP8 compliance.

* Keeps single blanklines used to breakup code sections.

* Cleans hanging indents and ensures they are visable to readers.

* Optionally reflow long comments including converting inline ones to standalone.

* Show diff of changes.

* Checks code before and after formatting is equivalent so errors cannot be introduced.

* Respects developers choice on linebreaking\line continuation within statments.

* Doesn't try to be too cleaver.


Installation
============

::

    pip install peprika


