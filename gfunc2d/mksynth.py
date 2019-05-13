import h5py
import numpy as np
import pandas as pd
from gfunc2d.gridtools import get_isochrone, get_gridparams

known_filters = ['J', 'H', 'Ks', #2MASS
                 'u', 'v', 'g', 'r', 'i' ,'z', #SkyMapper
                 'G', 'G_BPbr', 'G_BPft', 'G_RP'] #Gaia (DR2)

def generate_synth_stars(isogrid, outputfile, t_bursts, ns, feh_params,
                         IMF_alpha=2.35, rand_seed=1, extra_giants=0):
    """
    Generate synthetic sample of stars, save stellar parameters in hdf5-format.

    Parameters
    ----------
    isogrid : str
        Isochrone grid file (hdf5) to sample from.

    outputfile : str
        Name of file (hdf5) to store the parameters of the synthetic sample in.

    t_bursts : array
        Array with dimension (n, 3), where each row gives
        [t_low, t_high, probability] of a star formation burst (in Gyr).
        Can also be 1D array [t_low, t_high, probability] in which case the
        probability is ignored (since all stars must come from the one burst).

    ns : int
        Number of stars to generate.

    feh_params : array
        An array giving the mean and dispersion of the metallicites of the
        synthetic stars [feh_mean, feh_dispersion].
        The metallicities are drawn from a normal distribution
        N(feh_mean, feh_dispersion).

    IMF_alpha : float, optional
        Power law exponent to use for the initial mass function.
        Default is 2.35 (Salpeter IMF).

    rand_seed : int, optional
        Seed for np.random. Ensures that samples can be reproduced.
        Default value is 1.

    extra_giants : float, optional
        Option to artificially increase the number of giants in the sample.
        A number between 0 and 1 setting the final fraction of the total sample
        which will be forced to be giants.
        Default is 0 in which case no extra giants are added.
    """

    # This initialises the random number generator to a given state so that
    # results can be repeated
    np.random.seed(rand_seed)

    # Settings for the synthetic dataset
    single_burst = True if len(t_bursts.shape) == 1 else False
    feh_mean, feh_disp = feh_params
    config = {'t_bursts': t_bursts, 'IMF_alpha': IMF_alpha, 'ns': ns,
              'feh_mean': feh_mean, 'feh_disp': feh_disp,
              'seed': rand_seed, 'gridpath': isogrid}

    # Arrays to store true parameters
    tau = np.zeros(ns) # True ages (yr)
    feh = np.zeros(ns) # True [Fe/H]

    # Auxiliary arrays for generating true ages
    if not single_burst:
        prob = t_bursts[:, 2]
        prob = prob / np.sum(prob)
        n_bursts = len(prob)

    # Open isochrone grid
    gridfile = h5py.File(isogrid, 'r')

    # Get isochrone parameters and prepare dictionary with real data
    params = get_gridparams(gridfile)[0]
    data = {}
    # Prepare arrays for each parameter + the age
    for param in params + ['age', 'phase']:
        data[param] = np.zeros(ns)

    iv = 0 # Number of generated stars with valid isochrone
    ne = 0 # Number of generated stars that have evolved beyond isochrones
    # The evolutionary phase of the current star (simple dwarf or giant)
    phase_i = 0
    while iv < ns:
        # Define true age
        if single_burst:
            age = t_bursts[0] + (t_bursts[1]-t_bursts[0]) * np.random.rand()
        else:
            i_burst = np.random.choice(range(n_bursts), p=prob)
            age = t_bursts[i_burst, 0] +\
                  (t_bursts[i_burst, 1]-t_bursts[i_burst, 0]) * np.random.rand()
        feh_test = np.random.normal(feh_mean, feh_disp)

        # Get the isochrone for [Fe/H], age
        q, afa = get_isochrone(gridfile, 0.0, feh_test, age)

        # Find indices of lowest model-to-model temperature difference
        low_inds = np.argsort(np.diff(10**q['logT']))[:5]
        # Split between dwarf and giant at this index
        split_ind = int(np.median(low_inds))

        # Set the minimum mass depending on whether a star is forced to be
        # a giant
        if iv < ns*(1-extra_giants):
            # Minimum temperature to include (setting the minimum mass also)
            Teffmin_dwarf = 4500-500*feh_test
            idx_dwarf = np.argmin((np.abs(10**q['logT'][:split_ind]-Teffmin_dwarf)))
            m_min = q['Mini'][idx_dwarf]
            phase_i = 0
        else:
            m_min = q['Mini'][split_ind]
            phase_i = 1

        m = m_min * np.random.rand()**(-1/(IMF_alpha-1)) # True initial mass

        iso_age = afa[2] # True age
        q_mass = q['Mini']

        # If initial mass is in the valid range for the age
        if m < q_mass[-1]:
            # Interpolate along the isochrone to the given initial mass
            im = np.where(q_mass <= m)[0][-1]
            ip = np.where(q_mass > m)[0][0]
            # Now q_mass[im] <= m < q_mass[ip]
            h = (m - q_mass[im]) / (q_mass[ip] - q_mass[im])
            # Save the interpolated isochrone parameters for the chosen model
            for param in params:
                data[param][iv] = (1-h)*q[param][im] + h*q[param][ip]
            data['age'][iv] = iso_age
            data['phase'][iv] = phase_i

            iv += 1
        else:
            ne += 1

    print('Number of valid stars = ', iv)
    print('Number of discarded stars (too massive for the age) = ', ne)
    gridfile.close()

    # Open the file that the synthetic sample is saved in
    outfile = h5py.File(outputfile, 'w')

    # Save the config information
    for cparam in config:
        if config[cparam] is None:
            config[cparam] = 'None'
        outfile.create_dataset('config/'+cparam, data=config[cparam])

    # Save the stellar data
    for sparam in data:
        outfile.create_dataset('data/'+sparam,
                                data=data[sparam])
    outfile.close()


def make_synth_obs(synthfile, outputfile, obs_params, plx_distribution='SN'):
    '''
    Generate an input file for gfunc2D based on synthetic sample of stars.

    Parameters
    ----------
    synthfile : str
        File (hdf5) with data for a synthetic sample of stars.

    outputfile : str
        Output file for "observed" stellar parameters (text-file).

    obs_params : dict
        Dictionary with names of parameters to observe as keys and their
        uncertainties as values e.g. {'FeHini': 0.1, 'logg': 0.2, ...}.
        Parameter names must match the names in the isochrone grid file (this
        means that for temperatures, the name is 'logT'; The temperature will
        be saved in Kelvin, not as logarithm).

    plx_distribution : float, optional
        Value of the parallax. Can also give the string 'SN' in which case the
        parallaxes are given an exponential distribution to mimic the density
        of observed stars in the solar neighborhood, or 'Skymapper' which
        mimics the parallax distribution in SkyMapper data.
        Default value is 'SN'.
    '''

    # Check whether observed magnitudes should be calculated
    obs_mags = list(set(known_filters) & set(obs_params))
    if len(obs_mags) > 0 and 'plx' not in obs_params:
        raise ValueError('"plx" must be in obs_params to observe magnitudes')

    # Get the 'true' parameters of the synthetic stars
    true_data = {}
    synth_data = h5py.File(synthfile, 'r')
    ns = synth_data['config/ns'].value

    for oparam in obs_params:
        if oparam == 'plx':
            continue
        try:
            if oparam == 'logT':
                true_data[oparam] = 10**(synth_data['data/'+oparam][:])
            else:
                true_data[oparam] = synth_data['data/'+oparam][:]
        except:
            raise KeyError('Parameter ' + oparam + ' not in synthetic data...')
    synth_data.close()

    # If parallaxes are to be fitted, the true values are assumed based on
    # the input plx_distribution
    if 'plx' in obs_params:
        if plx_distribution == 'SN':
            # Approximate distance distribution of stars in the solar neighborhood
            plx_true = np.exp(np.random.normal(0.5637, 0.8767, ns))
        elif plx_distribution == 'Skymapper':
            plx_true = np.exp(np.random.normal(-0.255, 0.656, ns))
        else:
            # Else a constant value (given in plx_distribution)
            plx_true = plx_distribution*np.ones(ns)

        true_data['plx'] = plx_true

        # True distance modulus and magnitudes
        mu_true = 5 * np.log10(100/plx_true)
        app_mags_true = {x: [] for x in obs_mags}
        for mag in obs_mags:
            app_mags_true[mag] = true_data[mag] + mu_true

    # Prepare dictionary with observed parameters
    obs_data = {x: [] for x in obs_params}

    # Make observed data assuming Gaussian uncertainties
    for oparam in obs_data:
        if oparam in obs_mags:
            obs_data[oparam] = app_mags_true[oparam] + \
                               np.random.normal(0, obs_params[oparam], ns)
        elif oparam == 'plx' and plx_distribution == 'Skymapper':
            plx_rel_err_interval = np.arange(0.02, 0.21, 0.01)
            plx_rel_err_prob = np.exp(-14*plx_rel_err_interval)
            plx_rel_err_prob = plx_rel_err_prob / np.sum(plx_rel_err_prob)
            plx_rel_err = np.random.choice(plx_rel_err_interval, ns, p=plx_rel_err_prob)
            obs_data[oparam] = true_data[oparam] + \
                               np.random.normal(0, true_data[oparam]*plx_rel_err, ns)
        else:
            obs_data[oparam] = true_data[oparam] + \
                               np.random.normal(0, obs_params[oparam], ns)

    # Use pandas to organize the data and print it to a text file
    pd_data = pd.DataFrame.from_dict(obs_data)
    for i, column in enumerate(list(pd_data)[::-1]):
        if column == 'plx' and plx_distribution == 'Skymapper':
            pd_data.insert(len(obs_data)-i, column+'_unc',
                           true_data[column]*plx_rel_err)
        else:
            pd_data.insert(len(obs_data)-i, column+'_unc',
                           obs_params[column]*np.ones(ns))
    pd_data.to_csv(outputfile, index_label='#sid', sep='\t',
                   float_format='%10.4f')