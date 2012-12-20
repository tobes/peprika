from setuptools import setup, find_packages

setup(
    name = "Peprika",
    version = "0.1",
    description="Python script formatter",
    long_description="Try to cleanly format python code",
    keywords='peprika',
    author='Toby Dacre',
    author_email='toby.junk@gmail.com',
    url='http://github.com/tobes/peprika',
    license='Expat license',
    py_modules=['peprika'],
    namespace_packages=[],
    include_package_data=True,
    zip_safe=False,
    install_requires=[
        'setuptools',
        # -*- Extra requirements: -*-
        'colorama',
    ],
    entry_points={
        'console_scripts': [
            'peprika = peprika:main',
        ],
    },
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Environment :: Console',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Programming Language :: Python :: 2',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
)
