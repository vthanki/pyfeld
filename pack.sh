rm -rf pyfeld.egg-infoA
rm -rf dist
python setup.py build
python setup.py dist 
python setup.py sdist
python setup.py bdist_wheel --universal
python setup.py bdist_wheel
twine upload dist/*

